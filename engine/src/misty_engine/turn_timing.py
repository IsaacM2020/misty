"""Where the time goes in one voice turn (Season 3, Day 1). Numbers only, no text: one line per turn in voice.log.

A `Marks` is made when a chat message reaches the engine. `mark("name")` notes how many ms have passed since then.
`write()` appends `ENGINE_TIMING turn=<id> t0=<epoch s> since_start_s=<n> name=ms ...` next to the app's own
`TURN_TIMING` line, and `scripts/turn_timing_report.py` joins the two by turn id.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

_START = time.monotonic()
_NAME = re.compile(r"^[A-Za-z0-9_]{1,40}$")


class Marks:
    def __init__(self) -> None:
        self.t0 = time.time()
        self._p0 = time.perf_counter()
        self.items: list[tuple[str, int]] = []

    def mark(self, name: str) -> None:
        if _NAME.match(name):
            self.items.append((name, round((time.perf_counter() - self._p0) * 1000)))

    def line(self, turn: str) -> str:
        body = " ".join(f"{n}={ms}" for n, ms in self.items)
        return (f"ENGINE_TIMING turn={turn} t0={self.t0:.3f} since_start_s={time.monotonic() - _START:.0f} {body}").strip()

    def write(self, log_file: Path, turn: str) -> None:
        if not _NAME.match(turn):
            return
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{stamp} {self.line(turn)}\n")
        except OSError:
            pass                                          # timing must never break a turn
