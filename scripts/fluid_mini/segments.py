#!/usr/bin/env python3
"""Reads one 16 kHz mono wav with Fluid Speech Mini and prints {"segments": [{start, end, text}]} as JSON.
Pieces are cut at quiet points (about 30 s each, the model's own splitter), so a segment is one piece with its real times.
usage: segments.py <model dir> <wav>   (run by the engine with the Python in ~/Library/Application Support/Misty/fluid-mini)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcribe as T  # noqa: E402


def main() -> None:
    model = T.Model(sys.argv[1], 0, "cpu", spin=False)
    rate = model.cfg["sampling_rate"]
    wav = T.load_audio(sys.argv[2], rate)
    pieces = [wav] if len(wav) <= model.cfg["max_single_pass_seconds"] * rate else model.split_long(wav)
    out, at = [], 0
    for p in pieces:
        text = model.transcribe_pass(p).strip() if len(p) >= model.cfg["hop_length"] * 2 else ""
        if text:
            out.append({"start": at / rate, "end": (at + len(p)) / rate, "text": " ".join(text.split())})
        at += len(p)
    print(json.dumps({"segments": out}))


if __name__ == "__main__":
    main()
