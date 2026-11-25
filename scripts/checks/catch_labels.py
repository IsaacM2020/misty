#!/usr/bin/env python3
"""G1 second pass: Haiku (with thinking) solves each of the 50 fixtures BLIND (no label), then this prints where it disagrees with the label.
Every disagreement is then settled by a person: re-read the file and the label and decide who was wrong. Paid: about $0.09.

    engine/.venv/bin/python scripts/checks/catch_labels.py

Runs on a throwaway copy of the data folder (see _harness). It goes through the same door as the teaching model (`teach.ask.think`,
purpose `teach`); the `catch` purpose arrives in G3. It agrees when the flagged line matches (the kind is only noted).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402  (puts engine/src and tests/engine on sys.path)

SYSTEM = """You check a student's worked solution. You are given the text of a page: some page furniture (menus, "Share", "Saved to Drive"),
then the question, then his working, one step per line. Find a DEFINITE error in his working: a wrong sign, a wrong sum, a number copied
wrong from the question or data, a wrong or missing unit, or a wrong formula. Crossed out lines (~~like this~~), notes to himself, "??" and
shorthand are NOT errors. Spelling and style are never errors. If you are not sure, there is no error.
Count lines from the FIRST line of his working (the first step after the question) as line 1.
Reply with one JSON object only: {"error": true or false, "line": the line number or 0, "kind": "sign" | "arithmetic" | "copied" | "unit" | "formula" | ""}"""


def blind_prompt(case) -> str:
    return "Page text:\n" + case.text.strip()


def parse(text: str) -> dict:
    from misty_engine.teach.ask import parse_json
    try:
        d = parse_json(text)
    except (ValueError, json.JSONDecodeError):
        return {"error": False, "line": 0, "kind": ""}
    return {"error": bool(d.get("error")), "line": int(d.get("line") or 0) if str(d.get("line") or 0).lstrip("-").isdigit() else 0, "kind": str(d.get("kind") or "")}


def agrees(case, got: dict) -> bool:
    if not case.expect:
        return not got["error"]
    return bool(got["error"]) and got["line"] == case.expect["line"]


def _what(case, got: dict) -> str:
    if not case.expect:
        return f"invented an error at line {got['line']} ({got['kind']})"
    if not got["error"]:
        return f"missed the {case.expect['kind']} error at line {case.expect['line']}"
    return f"flagged line {got['line']} ({got['kind']}), label says line {case.expect['line']} ({case.expect['kind']})"


async def run(cases, judge) -> dict:
    results = await asyncio.gather(*(judge(c) for c in cases))
    bad, usd = [], 0.0
    for c, (got, cost) in zip(cases, results):
        usd += cost
        if not agrees(c, got):
            bad.append({"id": c.id, "what": _what(c, got), "note": c.note})
    return {"n": len(cases), "disagree": bad, "usd": usd}


async def _paid() -> int:
    from catchset import load
    from misty_engine.teach.ask import think
    from misty_engine.openrouter import user_approved
    ctx, home = _harness.temp_ctx(copy_live=True)
    try:
        async def judge(case):
            text, usd = await think(ctx, SYSTEM, blind_prompt(case), max_tokens=300)
            return parse(text), usd
        with user_approved():
            out = await run(load(), judge)
    finally:
        _harness.cleanup(home)
    print(f"{out['n']} files, {len(out['disagree'])} disagreements, ${out['usd']:.4f}")
    for d in out["disagree"]:
        print(f"  {d['id']}: {d['what']}   (label note: {d['note']})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_paid()))
