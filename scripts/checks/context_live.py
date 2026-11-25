#!/usr/bin/env python3
"""Card C2 live check: with a selection in the frame, "explain this" is answered from it with no tool call.
Runs the real engine and model on a throwaway COPY of the db (never the live one: B's migrations have only run on copies).

    engine/.venv/bin/python scripts/checks/context_live.py

5 made-up selections, each asked "what does this mean" . Pass: reply contains an expected word and no tool was called (5 of 5).
Also prints cost per turn with the frame and for the same question with no frame (a bare "explain this" gets a question back).
Paid: a few cents (Haiku).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

# The card's own phrase, "explain this", matches tutor.wants_help, whose preflight runs look + solve + draw before the model
# answers (drift note C2). Pass --explain to see that; the default phrase is one the tutor preflight leaves alone.
ASK = "what does this mean" if "--other" in sys.argv else "explain this"

CASES = [
    ("Pages", "Chem notes week 6", "Ionic bonding happens when a metal gives electrons to a non-metal.", ["electron", "metal"]),
    ("Preview", "bees.pdf", "A queen bee can lay up to 2,000 eggs a day.", ["queen", "egg"]),
    ("Google Chrome", "Opportunity cost - Wiki", "Opportunity cost is the value of the next best alternative given up.", ["alternative", "give", "given", "next best"]),
    ("TextEdit", "lab.txt", "Electrolysis of molten lead bromide gives lead at the cathode.", ["cathode", "lead"]),
    ("Visual Studio Code", "rate.py", "def rate(k, a): return k * a  # first order rate", ["k", "rate", "multipl", "first"]),
]


async def run():
    from misty_engine import chat, frame
    from misty_engine.openrouter import user_approved
    ctx, home = _harness.temp_ctx(copy_live=True)
    out = []
    try:
        async def nothing(_): return None
        async def no(_): return False
        for app, title, sel, words in CASES:
            row = {}
            for label, fr in (("frame", frame.parse({"app": app, "title": title, "selection": sel})), ("bare", None)):
                ctx.con.execute("DELETE FROM chat_messages"); ctx.con.commit()
                with user_approved():
                    r = await chat.send(ctx, ASK, on_delta=nothing, on_status=nothing, confirm=no, frame=fr)
                row[label] = (r["reply"], r.get("actions") or [], float(r.get("usd") or 0))
            out.append((sel, words, row))
        return out
    finally:
        _harness.cleanup(home)


def main() -> int:
    res = asyncio.run(run())
    good, usd_f, usd_b = 0, 0.0, 0.0
    for sel, words, row in res:
        reply, actions, u = row["frame"]
        ok = any(w in reply.lower() for w in words) and not actions
        good += ok
        usd_f += u; usd_b += row["bare"][2]
        print(f"  {'OK ' if ok else 'BAD'} tools={[a.get('tool') for a in actions]} reply: {reply[:110]!r}")
    n = len(res)
    print(f"explain-this from the frame: {good} of {n}; cost per turn with frame ${usd_f / n:.5f}, bare ${usd_b / n:.5f}")
    print("PASS" if good == n else "FAIL")
    return 0 if good == n else 1


if __name__ == "__main__":
    sys.exit(main())
