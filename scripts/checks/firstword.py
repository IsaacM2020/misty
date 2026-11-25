#!/usr/bin/env python3
"""First-word check: median time from the end of his speech to her first sound, over the last 30 plain voice turns
(no tool call), read from the voice log by scripts/turn_timing_report.py. Passes when it is not more than 10% worse than
the number recorded in projects/misty/baselines.md (line `first_word_median_ms: N`, written by the B0 card).
Prints the numbers only. Free. Exit 1 on fail; exit 0 with a note when there are too few turns to judge."""
from __future__ import annotations

import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path.home() / "Notes" / "projects" / "misty" / "baselines.md"


def current() -> tuple[float | None, int]:
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "turn_timing_report.py")], capture_output=True, text=True).stdout
    vals = []
    for line in out.splitlines():
        if re.match(r"^v[0-9a-f]{8} ", line) and "tools=0ms" in line:
            t = line.split()
            i = next(k for k, x in enumerate(t) if x.endswith("ch"))
            vals.append(int(t[i - 1]))
    vals = vals[-30:]
    return (statistics.median(vals) if vals else None), len(vals)


def main() -> int:
    m = re.search(r"first_word_median_ms:\s*(\d+)", BASE.read_text()) if BASE.exists() else None
    if not m:
        print("first word: no baseline recorded yet (baselines.md)")
        return 1
    base = float(m.group(1))
    now, n = current()
    if now is None or n < 10:
        print(f"first word: only {n} plain turns in the log, cannot judge (baseline {base:.0f} ms)")
        return 0
    ok = now <= base * 1.10
    print(f"first word: median {now:.0f} ms over the last {n} plain turns, baseline {base:.0f} ms, limit {base * 1.1:.0f} ms")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
