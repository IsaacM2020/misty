#!/usr/bin/env python3
"""Fluid Speech transcription with ONNX Runtime. Needs only: onnxruntime, numpy, soundfile
(+ soxr or scipy when the audio is not already at the model's sample rate).

usage: transcribe.py <model dir> <audio> [<audio> ...] [--threads N] [--no-spin] [--provider cpu|cuda] [--timing] [--repeat N]
       transcribe.py <model dir> --list manifest.jsonl --out hyps.jsonl      (lines: {"id": ..., "audio": ...})

The model dir holds encoder.onnx (+ frontend.onnx when the conv front end is split out), decoder.onnx, joint.onnx, tokenizer.json, config.json,
mel_filters.npy. A clip up to config max_single_pass_seconds goes through the encoder in one call; longer audio
is cut at quiet points into pieces of about 30 s that are transcribed one after another."""
import argparse
import json
import os
import sys
import time

import numpy as np
import onnxruntime as ort
import soundfile as sf

def release_memory():
    """glibc keeps freed load-time and activation buffers; hand them back (Linux only, a no-op elsewhere)."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


LOG_ZERO_GUARD = 2.0 ** -24
NORM_EPS = 1e-5


def load_audio(path, rate):
    wav, sr = sf.read(path, dtype="float32", always_2d=True)
    wav = wav[:, 0] if wav.shape[1] == 1 else wav.mean(axis=1)
    if sr != rate:
        try:
            import soxr
            wav = soxr.resample(wav, sr, rate).astype(np.float32)
        except ImportError:
            from math import gcd
            from scipy.signal import resample_poly
            g = gcd(int(sr), int(rate))
            wav = resample_poly(wav, rate // g, sr // g).astype(np.float32)
    return np.ascontiguousarray(wav, dtype=np.float32)


def log_mel(wav, cfg, filters):
    """Same features as the Hugging Face ParakeetFeatureExtractor: returns ([1, T, mels] fp32, [1, T] int64)."""
    n_fft, hop, win = cfg["n_fft"], cfg["hop_length"], cfg["win_length"]
    x = wav.astype(np.float32)
    if cfg.get("preemphasis"):
        x = np.concatenate([x[:1], x[1:] - np.float32(cfg["preemphasis"]) * x[:-1]])
    window = np.zeros(n_fft, dtype=np.float64)
    left = (n_fft - win) // 2
    n = np.arange(win, dtype=np.float64)
    window[left:left + win] = (0.5 - 0.5 * np.cos(2.0 * np.pi * n / (win - 1))).astype(np.float32)
    padded = np.pad(x, n_fft // 2)
    frames = 1 + len(x) // hop
    mel = np.empty((frames, filters.shape[0]), dtype=np.float32)
    offsets = np.arange(n_fft)[None, :]
    for start in range(0, frames, 256):  # in blocks: the STFT of a whole clip at once costs tens of MB
        idx = offsets + hop * np.arange(start, min(start + 256, frames))[:, None]
        spec = np.fft.rfft(padded[idx].astype(np.float64) * window, axis=1)
        power = (spec.real ** 2 + spec.imag ** 2).astype(np.float32)
        mel[start:start + len(idx)] = np.log(power @ filters.T + np.float32(LOG_ZERO_GUARD))
    valid = min(len(x) // hop, frames)
    mean = mel[:valid].sum(axis=0, dtype=np.float32) / np.float32(valid)
    var = ((mel[:valid] - mean) ** 2).sum(axis=0, dtype=np.float32) / np.float32(max(valid - 1, 1))
    out = (mel - mean) / (np.sqrt(var) + np.float32(NORM_EPS))
    out[valid:] = 0.0
    mask = (np.arange(frames) < valid).astype(np.int64)
    return out[None].astype(np.float32), mask[None]


class Tokenizer:
    def __init__(self, path, cfg):
        t = json.load(open(path, encoding="utf-8"))
        self.pieces = {i: s for s, i in t["model"]["vocab"].items()}
        skip = {cfg["blank_token_id"], cfg["pad_token_id"]}
        for a in t.get("added_tokens", []):
            self.pieces[a["id"]] = a["content"]
            if a.get("special"):
                skip.add(a["id"])
        self.skip = skip
        dec = t.get("decoder") or {}
        self.space = dec.get("replacement", "▁")

    def decode(self, ids):
        text = "".join(self.pieces.get(i, "") for i in ids if i not in self.skip).replace(self.space, " ")
        return text[1:] if text.startswith(" ") else text


class Model:
    def __init__(self, model_dir, threads=0, provider="cpu", spin=True):
        self.cfg = json.load(open(os.path.join(model_dir, "config.json")))
        self.filters = np.load(os.path.join(model_dir, "mel_filters.npy")).astype(np.float32)
        self.tokenizer = Tokenizer(os.path.join(model_dir, "tokenizer.json"), self.cfg)
        providers = ["CPUExecutionProvider"]
        if provider == "cuda":
            if hasattr(ort, "preload_dlls"):
                ort.preload_dlls()
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        so = ort.SessionOptions()
        # activations of the conv front end dominate memory; without the arena they are freed between runs
        so.enable_cpu_mem_arena = False
        so.enable_mem_pattern = False
        if threads:
            so.intra_op_num_threads = threads
        if not spin:
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        small = ort.SessionOptions()
        small.intra_op_num_threads = 1
        small.enable_cpu_mem_arena = False
        small.enable_mem_pattern = False
        self.encoder = ort.InferenceSession(os.path.join(model_dir, "encoder.onnx"), so, providers=providers)
        front = os.path.join(model_dir, "frontend.onnx")
        self.frontend = ort.InferenceSession(front, so, providers=providers) if os.path.exists(front) else None
        self.decoder = ort.InferenceSession(os.path.join(model_dir, "decoder.onnx"), small, providers=["CPUExecutionProvider"])
        self.joint = ort.InferenceSession(os.path.join(model_dir, "joint.onnx"), small, providers=["CPUExecutionProvider"])
        self.timing = {"mel": 0.0, "encoder": 0.0, "decode": 0.0}
        release_memory()

    def subsampled(self, frames):
        cfg = self.cfg
        pad = (cfg["subsampling_kernel"] - 1) // 2 * 2 - cfg["subsampling_kernel"]
        for _ in range(cfg["subsampling_layers"]):
            frames = int(np.floor((frames + pad) / cfg["subsampling_stride"] + 1.0))
        return frames

    def encode(self, feats, mask):
        """([T', hidden] fp32, valid frames). With frontend.onnx the conv front end runs in time chunks (same
        result, far smaller activations): each chunk carries one output frame of context on both sides."""
        if self.frontend is None:
            enc, enc_len = self.encoder.run(None, {"input_features": feats, "attention_mask": mask})
            return enc[0], int(enc_len[0])
        cfg = self.cfg
        factor = cfg["subsampling_stride"] ** cfg["subsampling_layers"]
        frames, total, step = feats.shape[1], self.subsampled(feats.shape[1]), cfg["frontend_chunk_frames"]
        parts = []
        for a in range(0, total, step):
            b = min(a + step, total)
            s = factor * (a - 1) if a else 0
            e = min(frames, factor * (b + 1))
            y = self.frontend.run(None, {"input_features": feats[:, s:e], "attention_mask": mask[:, s:e]})[0]
            skip = 1 if a else 0
            parts.append(y[:, skip:skip + (b - a)])
        hidden = np.concatenate(parts, axis=1) if len(parts) > 1 else parts[0]
        valid = self.subsampled(int(mask.sum()))
        enc = self.encoder.run(None, {"hidden_states": hidden, "length": np.array([valid], dtype=np.int64)})[0]
        return enc[0], valid

    def greedy_tdt(self, enc, valid_len):
        """Greedy token-and-duration decoding, the loop of transformers' ParakeetForTDT.generate for one clip."""
        cfg = self.cfg
        vocab, blank, eos = cfg["vocab_size"], cfg["blank_token_id"], cfg.get("eos_token_id")
        durations = cfg["durations"]
        state = np.zeros((cfg["decoder_layers"], 1, cfg["decoder_hidden_size"]), dtype=np.float32)
        dec_out, h, c = self.decoder.run(None, {"token": np.array([[blank]], dtype=np.int64), "h_in": state, "c_in": state})
        frames = enc.shape[0]
        budget = cfg["max_symbols_per_step"] * frames - 1
        tokens, t = [], 0
        while True:
            logits = self.joint.run(None, {"encoder_frame": enc[min(t, frames - 1)][None], "decoder_out": dec_out})[0][0]
            token = int(logits[:vocab].argmax())
            step = durations[int(logits[vocab:].argmax())]
            if token == blank and step == 0:
                step = 1
            tokens.append(token)
            t += step
            if t >= valid_len or len(tokens) >= budget or token == eos:
                return tokens
            if token != blank:
                dec_out, h, c = self.decoder.run(None, {"token": np.array([[token]], dtype=np.int64), "h_in": h, "c_in": c})

    def transcribe_pass(self, wav):
        t0 = time.perf_counter()
        feats, mask = log_mel(wav, self.cfg, self.filters)
        t1 = time.perf_counter()
        enc, enc_len = self.encode(feats, mask)
        del feats
        release_memory()
        t2 = time.perf_counter()
        tokens = self.greedy_tdt(enc, enc_len)
        t3 = time.perf_counter()
        self.timing["mel"] += t1 - t0
        self.timing["encoder"] += t2 - t1
        self.timing["decode"] += t3 - t2
        return self.tokenizer.decode(tokens)

    def split_long(self, wav, target=30.0, search=10.0):
        rate = self.cfg["sampling_rate"]
        hop = rate // 50
        energy = np.sqrt(np.add.reduceat(wav.astype(np.float64) ** 2, np.arange(0, len(wav), hop)) / hop)
        smooth = np.convolve(energy, np.ones(10) / 10, mode="same")
        pieces, start = [], 0
        while len(wav) - start > self.cfg["max_single_pass_seconds"] * rate:
            lo = (start + int((target - search / 2) * rate)) // hop
            hi = (start + int((target + search / 2) * rate)) // hop
            cut = (lo + int(smooth[lo:hi].argmin())) * hop
            pieces.append(wav[start:cut])
            start = cut
        pieces.append(wav[start:])
        return pieces

    def transcribe(self, wav):
        rate = self.cfg["sampling_rate"]
        if len(wav) < self.cfg["hop_length"] * 2:
            return ""
        if len(wav) <= self.cfg["max_single_pass_seconds"] * rate:
            return self.transcribe_pass(wav)
        return " ".join(s for s in (self.transcribe_pass(p).strip() for p in self.split_long(wav)) if s)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("model_dir")
    p.add_argument("audio", nargs="*")
    p.add_argument("--list")
    p.add_argument("--out")
    p.add_argument("--shard", default="0/1", help="with --list: K/N = every Nth line starting at K")
    p.add_argument("--threads", type=int, default=0)
    p.add_argument("--provider", default="cpu", choices=["cpu", "cuda"])
    p.add_argument("--no-spin", action="store_true", help="worker threads sleep instead of spinning (busy machines)")
    p.add_argument("--timing", action="store_true")
    p.add_argument("--repeat", type=int, default=1)
    args = p.parse_args()

    t0 = time.perf_counter()
    model = Model(args.model_dir, args.threads, args.provider, spin=not args.no_spin)
    load_s = time.perf_counter() - t0
    rate = model.cfg["sampling_rate"]

    if args.list:
        done = set()
        if args.out and os.path.exists(args.out):
            done = {json.loads(line)["id"] for line in open(args.out, encoding="utf-8")}
        out = open(args.out, "a", encoding="utf-8") if args.out else sys.stdout
        k, every = (int(x) for x in args.shard.split("/"))
        for n, line in enumerate(open(args.list, encoding="utf-8").read().splitlines()[k::every]):
            row = json.loads(line)
            if row["id"] in done:
                continue
            out.write(json.dumps({"id": row["id"], "hyp": model.transcribe(load_audio(row["audio"], rate))}, ensure_ascii=False) + "\n")
            out.flush()
            if args.timing and n % 200 == 0:
                print(f"{n} done", file=sys.stderr, flush=True)
    for path in args.audio:
        wav = load_audio(path, rate)
        runs = []
        for _ in range(args.repeat):
            model.timing = {"mel": 0.0, "encoder": 0.0, "decode": 0.0}
            t = time.perf_counter()
            text = model.transcribe(wav)
            runs.append((time.perf_counter() - t, dict(model.timing)))
        print(text)
        if args.timing:
            audio_s = len(wav) / rate
            total, parts = sorted(runs, key=lambda r: r[0])[len(runs) // 2]
            print(json.dumps({"audio_s": round(audio_s, 2), "load_s": round(load_s, 2), "first_run_s": round(runs[0][0], 3),
                              "median_run_s": round(total, 3), "seconds_per_15s_audio": round(total * 15.0 / audio_s, 3),
                              "mel_s": round(parts["mel"], 3), "encoder_s": round(parts["encoder"], 3),
                              "decode_s": round(parts["decode"], 3), "threads": args.threads or "default",
                              "encoder_providers": model.encoder.get_providers(),
                              "torch_imported": "torch" in sys.modules, "transformers_imported": "transformers" in sys.modules}),
                  file=sys.stderr)


if __name__ == "__main__":
    main()
