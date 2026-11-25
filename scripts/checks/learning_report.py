#!/usr/bin/env python3
"""E17: what the probes say he learned. Free, reads the tables only (no model, no spend).

    engine/.venv/bin/python scripts/checks/learning_report.py [--db <path to misty.db>]

Prints per topic the before, after and week scores, then the averages across topics that have both `before` and `after`.
A topic is not counted when he skipped the `before`, or when its bank had under 9 usable questions (no probe was made). The counts are printed.
Used by E19 (the bar: after at least 80%, week at least 70%).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _latest(con, topic_id: str, set_name: str):
    return con.execute("SELECT * FROM teach_probes WHERE topic_id=? AND set_name=? AND status='done' ORDER BY at DESC, rowid DESC LIMIT 1",
                       (topic_id, set_name)).fetchone()


def _frac(r) -> float | None:
    return r["score"] / r["out_of"] if r is not None and r["out_of"] else None


def build(con) -> dict:
    con.row_factory = sqlite3.Row
    topics = con.execute("SELECT id, title FROM teach_topics WHERE status='active' ORDER BY created_at, id").fetchall()
    rows, skipped, small = [], 0, 0
    for t in topics:
        b, a, w = (_latest(con, t["id"], n) for n in ("before", "after", "week"))
        n_skip = con.execute("SELECT COUNT(*) FROM teach_probes WHERE topic_id=? AND set_name='before' AND status='skipped'", (t["id"],)).fetchone()[0]
        usable = con.execute("SELECT COUNT(*) FROM teach_questions WHERE topic_id=? AND source='bank' AND status='usable'", (t["id"],)).fetchone()[0]
        if b is None and a is None and w is None and not n_skip:
            if usable and usable < 9:
                small += 1
            continue
        row = {"topic": t["title"], "before": b, "after": a, "week": w, "skipped": bool(n_skip) and b is None, "usable": usable}
        if row["skipped"]:
            skipped += 1
        rows.append(row)
    both = [r for r in rows if r["before"] is not None and r["after"] is not None]
    avg = lambda xs: (sum(xs) / len(xs)) if xs else None
    weeks = [_frac(r["week"]) for r in both if r["week"] is not None]
    return {"rows": rows, "counted": len(both), "skipped_before": skipped, "small_bank": small,
            "before_avg": avg([_frac(r["before"]) for r in both]), "after_avg": avg([_frac(r["after"]) for r in both]),
            "week_avg": avg(weeks), "week_n": len(weeks)}


def _s(r) -> str:
    return f"{r['score']} of {r['out_of']}" if r is not None else "-"


def _p(x) -> str:
    return "n/a" if x is None else f"{round(x * 100)}%"


def render(rep: dict) -> str:
    out = ["Learning (probes)", ""]
    for r in rep["rows"]:
        note = "  (skipped the before: not counted)" if r["skipped"] else ""
        out.append(f"  {r['topic']}: before {_s(r['before'])}, after {_s(r['after'])}, week {_s(r['week'])}{note}")
    if not rep["rows"]:
        out.append("  no probes yet")
    out += ["", f"Counted (both before and after): {rep['counted']}",
            f"Not counted: {rep['skipped_before']} skipped the before, {rep['small_bank']} with under 9 bank questions",
            f"Before average: {_p(rep['before_avg'])}", f"After average: {_p(rep['after_avg'])} (bar: 80%)",
            f"Week average: {_p(rep['week_avg'])} over {rep['week_n']} topics (bar: 70%)"]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "Library" / "Application Support" / "Misty" / "misty.db"))
    a = ap.parse_args()
    if not Path(a.db).exists():
        print(f"no database at {a.db}", file=sys.stderr)
        return 1
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    print(render(build(con)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
