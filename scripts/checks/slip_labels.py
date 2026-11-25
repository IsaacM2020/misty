#!/usr/bin/env python3
"""E12 scored check: does the mistake label agree with his own? PAID (30 small Haiku calls, about $0.01).

    engine/.venv/bin/python scripts/checks/slip_labels.py --dump 30 > labels.yaml     # free: his past wrong answers, to fill in `kind`
    engine/.venv/bin/python scripts/checks/slip_labels.py --labels labels.yaml --max-usd 0.02

`kind` is one of didnt_know, slipped, wrong_method, misread (or unsure when even he can't tell: those rows are skipped).
Bar: agrees on at least 25 of 30. If it is lower, the list of kinds or the prompt changes, not the bar.
Runs on a throwaway copy of the live db (its own spend meter). Exit 1 under the bar or over the budget.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402


def dump(n: int) -> int:
    db = sqlite3.connect(f"file:{Path.home() / 'Misty' / 'misty.db'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT id, question, his_answer, right_answer FROM teach_slips WHERE question!='' ORDER BY RANDOM() LIMIT ?", (n,)).fetchall()
    except sqlite3.OperationalError:
        print("the live db has no teach_slips yet: the app has not run the teaching code, so there is nothing to label", file=sys.stderr)
        return 1
    for r in rows:
        print(f"- id: {r['id']}\n  question: {json.dumps(r['question'])}\n  his_answer: {json.dumps(r['his_answer'])}\n  right_answer: {json.dumps(r['right_answer'])}\n  kind: ")
    print(f"# {len(rows)} rows", file=sys.stderr)
    return 0


async def check(a) -> int:
    import yaml
    from misty_engine.openrouter import user_approved
    from misty_engine.teach import slips
    labels = [x for x in yaml.safe_load(Path(a.labels).read_text()) if x.get("kind") in slips.KINDS]
    ctx, home = _harness.temp_ctx(copy_live=True)
    spent = 0.0
    base = None
    agree, rows = 0, []
    try:
        with user_approved():
            for x in labels:
                if spent >= a.max_usd:
                    break
                if base is None:
                    base = float(ctx.con.execute("SELECT COALESCE(SUM(usd),0) FROM spend").fetchone()[0])
                got = await slips.label(ctx, x["id"])
                spent = float(ctx.con.execute("SELECT COALESCE(SUM(usd),0) FROM spend").fetchone()[0]) - base
                agree += got == x["kind"]
                rows.append((x["id"], x["kind"], got))
    finally:
        _harness.cleanup(home)
    for i, want, got in rows:
        print(f"{'ok ' if want == got else 'BAD'} {i}: his {want:<13} mine {got or '(empty)'}")
    bar = min(25, len(labels) * 25 // 30)
    print(f"\nagrees on {agree} of {len(rows)} (bar {bar} of {len(labels)}); paid ${spent:.4f} (limit ${a.max_usd:.2f})")
    ok = len(rows) == len(labels) and agree >= bar and spent <= a.max_usd
    print("CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", type=int)
    ap.add_argument("--labels")
    ap.add_argument("--max-usd", type=float, default=0.02)
    a = ap.parse_args()
    if a.dump:
        return dump(a.dump)
    if not a.labels:
        ap.error("--dump N or --labels FILE")
    return asyncio.run(check(a))


if __name__ == "__main__":
    sys.exit(main())
