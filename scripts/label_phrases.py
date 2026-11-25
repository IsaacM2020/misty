#!/usr/bin/env python3
"""F6: label the phrases the mic heard. About 15 minutes, in the terminal, on this Mac.

    engine/.venv/bin/python scripts/label_phrases.py [--db ~/Misty/misty.db] [--out ~/Misty/checks/addressed.yaml]

One phrase at a time, with her last way of hearing it (the outcome, whether her name was in it, whether the follow-up window was open,
how long since her last reply). Press one key and Enter:  f = it was for her   n = not for her   s = skip   q = quit.
It keeps going where it stopped: a labelled phrase never comes back, a skipped one does next time. Phrases come balanced (one that
reached her, one she dropped, and so on) so the set is not 150 times "not". At the end it writes the labelled set to
~/Misty/checks/addressed.yaml (outside the repo) and prints the count per label. The bar for F7: 150 labelled, 40 each way.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine" / "src"))
from misty_engine import phrases  # noqa: E402

HOME = Path.home() / "Misty"
KEYS = {"f": "for_her", "n": "not_for_her"}


def _show(p: dict, i: int, total: int, put) -> None:
    since = "no reply before" if p["since_reply"] is None else f"{p['since_reply']:.0f} s after her last reply"
    bits = [f"outcome: {p['outcome']}", since, "her name was in it" if p["named"] else "no name", "follow-up window open" if p["followup"] else "window closed"]
    put(f"\n[{i}/{total}]  {p['text']}\n        ({'; '.join(bits)})")


def run(con: sqlite3.Connection, ask=input, put=print, out: Path | None = None, now: float | None = None, limit: int = phrases.TARGET) -> dict:
    """The loop. `ask` and `put` are swapped in tests. Returns {labelled, skipped} for this sitting."""
    con.row_factory = sqlite3.Row
    out = out or HOME / "checks" / "addressed.yaml"
    queue = phrases.queue(con, limit)
    done = skipped = 0
    for i, p in enumerate(queue, 1):
        _show(p, i, len(queue), put)
        while True:
            k = str(ask("f = for her, n = not, s = skip, q = quit > ")).strip().lower()[:1]
            if k in KEYS:
                phrases.set_label(con, p["id"], KEYS[k], time.time() if now is None else now)
                done += 1
                break
            if k == "s":
                skipped += 1
                break
            if k == "q":
                queue = []
                break
        if not queue:
            break
    c = phrases.export(con, out)
    put(f"\nThis sitting: {done} labelled, {skipped} skipped. In all: {c['total']} labelled "
        f"({c['for_her']} for her, {c['not_for_her']} not). "
        + ("The bar is met (150, at least 40 each way)." if c["bar_met"] else "The bar needs 150, at least 40 each way.")
        + f"\nWritten to {out}")
    return {"labelled": done, "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(HOME / "misty.db"))
    ap.add_argument("--out", default=str(HOME / "checks" / "addressed.yaml"))
    a = ap.parse_args()
    if not Path(a.db).exists():
        print(f"no database at {a.db}", file=sys.stderr)
        return 1
    con = sqlite3.connect(a.db, isolation_level=None)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='heard_phrases'").fetchone():
        print("nothing collected yet: turn it on first (say \"keep what you hear\"), use ambient mode for a few days, then come back.")
        return 0
    run(con, out=Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
