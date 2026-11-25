#!/usr/bin/env python3
"""Fluid Speech Mini as a long-lived worker: the model loads once, then every line on stdin is the path of a 16 kHz wav and the
reply is one JSON line on stdout. Saves the ~0.45 s of Python start and model load that a fresh process paid for every phrase.

usage: serve.py <model dir>
  stdout: {"ready": true} once loaded and warmed, then one {"text": "..."} (or {"error": "..."}) per request line.
  stdin closing ends it, so it never outlives the app."""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcribe import Model, load_audio  # noqa: E402


def say(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    model = Model(sys.argv[1])
    rate = model.cfg["sampling_rate"]
    model.transcribe(np.zeros(rate, dtype=np.float32))          # first run pays the one-off setup
    say({"ready": True})
    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        try:
            say({"text": model.transcribe(load_audio(path, rate)).strip()})
        except Exception as e:  # noqa: BLE001
            say({"error": type(e).__name__})


if __name__ == "__main__":
    main()
