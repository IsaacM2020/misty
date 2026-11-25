"""Add a Gradium voice file as an app voice.

Gradium voice files store `speaker_wavs` latents ([1, 512, T]). The app's Core ML Phonon only reads the
projected `emb` form ([1, T, 512]), and the bundle has no speaker projection. This applies the projection
from the full phonon-31m-en checkpoint in the HF cache. Checked: the 4 official phonon-voices files come out
byte-equal to the app's Zoey, Freya, Hugh and Miles. A file that already holds `emb` is copied as is.

Usage (restart EDITH after):
  engine/.venv/bin/python -I scripts/add_voice.py IN.safetensors NAME
Writes voices/NAME.safetensors into the app source models and the installed copy, and updates both bundle.json.
"""
import glob
import hashlib
import json
import os
import struct
import sys

import numpy as np


def load(p):
    b = open(p, "rb").read()
    n = struct.unpack("<Q", b[:8])[0]
    h = json.loads(b[8:8 + n])
    d = b[8 + n:]
    out = {}
    for k, v in h.items():
        if k == "__metadata__":
            continue
        s, e = v["data_offsets"]
        raw = d[s:e]
        if v["dtype"] == "F32":
            a = np.frombuffer(raw, np.float32)
        elif v["dtype"] == "BF16":
            a = (np.frombuffer(raw, np.uint16).astype(np.uint32) << 16).view(np.float32)
        else:
            raise ValueError(f"{k}: dtype {v['dtype']} not handled")
        out[k] = a.reshape(v["shape"])
    return out


def save(p, tensors):
    h, off, blobs = {}, 0, []
    for k, a in tensors.items():
        bb = np.ascontiguousarray(a, np.float32).tobytes()
        h[k] = {"dtype": "F32", "shape": list(a.shape), "data_offsets": [off, off + len(bb)]}
        off += len(bb)
        blobs.append(bb)
    hj = json.dumps(h, separators=(",", ":")).encode()
    hj += b" " * ((8 - len(hj) % 8) % 8)
    open(p, "wb").write(struct.pack("<Q", len(hj)) + hj + b"".join(blobs))


def projection():
    hub = os.path.expanduser("~/.cache/huggingface/hub")
    paths = glob.glob(hub + "/models--gradium--phonon-31m-en/snapshots/*/model.safetensors")
    if not paths:
        sys.exit("phonon-31m-en checkpoint not in the HF cache")
    return load(paths[0])["flow_lm.condition_provider.conditioners.speaker_wavs.output_proj.weight"]


def to_emb(t):
    if "emb" in t:
        return t["emb"]
    lat = t["speaker_wavs"]                       # [1, C, T]
    return (lat[0].T @ projection().T)[None]      # [1, T, 512]


def main(src, name):
    e = to_emb(load(src))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs = [root + "/app/VoiceModels/PhononModels",
            os.path.expanduser("~/Library/Application Support/PhononTTS/models")]
    rel = f"voices/{name}.safetensors"
    for d in dirs:
        p = f"{d}/{rel}"
        save(p, {"emb": e})
        sha = hashlib.sha256(open(p, "rb").read()).hexdigest()
        j = json.load(open(d + "/bundle.json"))
        j["files"] = [f for f in j["files"] if f["path"] != rel]
        j["files"].append({"path": rel, "sha256": sha, "size": os.path.getsize(p)})
        j["files"].sort(key=lambda f: f["path"])
        json.dump(j, open(d + "/bundle.json", "w"), indent=2)
        print("wrote", p)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
