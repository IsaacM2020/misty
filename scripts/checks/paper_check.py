#!/usr/bin/env python3
"""E13 scored check: do her marks agree with his (or a teacher's)? PAID (one parse call per 3 paper parts, one marking call per question, about $0.02).

    engine/.venv/bin/python scripts/checks/paper_check.py --paper <paper file> --scheme <mark scheme file> \\
        --answers answers.yaml --teacher-marks marks.yaml --max-usd 0.05

answers.yaml:  {"1": "his answer to question 1", "2": "..."}     (a missing number or an empty answer counts as no answer)
marks.yaml:    {"1": 2, "2": 1, ...}                              (the marks a teacher, or he, gave each question)
Bar: her total is within 1 mark in 10 of the section's marks (a 10 mark section: her total differs by at most 1). It runs on a fresh throwaway db
with one subject and no topics, so no evidence is written. Exit 1 over the bar or over the budget.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402


async def check(a) -> int:
    import json

    import yaml
    from misty_engine.openrouter import user_approved
    from misty_engine.teach import map as tm
    from misty_engine.teach import paper, sources
    answers = {str(k): str(v or "") for k, v in (yaml.safe_load(Path(a.answers).read_text()) or {}).items()}
    teacher = {str(k): float(v) for k, v in (yaml.safe_load(Path(a.teacher_marks).read_text()) or {}).items()}
    ctx, home = _harness.temp_ctx(copy_live=False)
    try:
        with user_approved():
            ps = await sources.add(ctx, a.paper, "file")
            ss = await sources.add(ctx, a.scheme, "file")
            sub = tm.add_subject(ctx, "paper_check", "Paper check")["id"]
            p = paper.pair(ctx, ps["id"], ss["id"], subject_id=sub)
            r = await paper.parse(ctx, p["paper_id"])
            if not r["ok"]:
                print("the paper did not parse:", r)
                return 1
            print(f"parsed {r['questions']} questions" + (f"; {r['say']}" if r["say"] else ""))
            qs = json.loads(ctx.con.execute("SELECT questions_json FROM teach_papers WHERE id=?", (p["paper_id"],)).fetchone()[0])["questions"]
            st = await paper.start(ctx, sub, sum(q["marks"] for q in qs))
            if not st["ok"]:
                print("could not start:", st)
                return 1
            out = None
            for q in qs:
                out = await paper.on_message(ctx, answers.get(q["no"], "") or "skip")
            spent = float(ctx.con.execute("SELECT COALESCE(SUM(usd),0) FROM spend").fetchone()[0])
    finally:
        _harness.cleanup(home)
    if not out or not out.get("finished"):
        print("the run did not finish:", out)
        return 1
    diff = 0.0
    for no, m in out["marks"].items():
        want = teacher.get(no)
        diff += abs(m["got"] - want) if want is not None else 0
        print(f"Q{no}: hers {m['got']} of {m['of']}, his {want if want is not None else '(none)'}   missed: {'; '.join(m['missed']) or '-'}")
    bar = math.ceil(out["out_of"] / 10)
    ok = diff <= bar and spent <= a.max_usd
    print(f"\nher total {out['total']} of {out['out_of']}; per question differences add up to {diff:g} (bar {bar}); paid ${spent:.4f} (limit ${a.max_usd:.2f})")
    print("CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", required=True)
    ap.add_argument("--scheme", required=True)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--teacher-marks", required=True)
    ap.add_argument("--max-usd", type=float, default=0.05)
    return asyncio.run(check(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
