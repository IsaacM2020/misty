#!/usr/bin/env python3
"""Ways on every surface (card B5). Sets three ways on a throwaway copy of the db (address "boss", tone dry, never say
"good morning"), then makes 10 morning briefs, 10 follow lines, 10 chat replies and 10 voice replies with the real model.

    engine/.venv/bin/python scripts/checks/ways_surfaces.py [--n 10]

Counts, per surface: lines that contain "good morning" (bar: 0 of 40 overall), and of the lines that address him at all
(contain "boss" or his name) how many used "boss" and not his name (bar: at least 9 of 10 per surface, read as 90%).
Paid: a few cents (Haiku), on a throwaway db with its own meter. Exit 1 if the bar is missed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

BANNED = "good morning"

FALLBACK_FACTS = {"day": "Tuesday 17 November", "now": "07:10",
                  "calendar_today": ["08:00-09:00 Physics class", "15:00 Chess club"],
                  "new_mail": [{"from": "Ms Smitha", "subject": "IA moved", "text": "The IA is now due Friday."}],
                  "watching_found": [], "told_overnight": [], "due_soon": [{"what": "Finish the IA draft", "subject": "Physics", "kind": "homework", "due": "Friday"}],
                  "goals": [], "priorities": [], "worked_on_yesterday": [], "openable": []}

FOLLOW_CASES = [
    ("Man City matches", [{"source": "BBC", "title": "Man City 2-1 Inter", "when": "2 h ago", "text": "City won 2-1 with a late goal from Haaland."}]),
    ("Google AI news", [{"source": "blog.google", "title": "Gemini 4 Carbon launches", "when": "1 h ago", "text": "Gemini 4 Carbon is now available to everyone."}]),
    ("school mail from Ms Smitha", [{"source": "Gmail", "title": "IA deadline moved", "when": "30 min ago", "text": "The Physics IA is now due Friday, not Monday."}]),
    ("SpaceX launches", [{"source": "NASA", "title": "Starship flight 14", "when": "3 h ago", "text": "Starship flight 14 launched and the booster was caught."}]),
    ("IB exam dates", [{"source": "ibo.org", "title": "May 2028 timetable", "when": "today", "text": "The May 2028 exam timetable has been published."}]),
]

CHAT = ["What's on my plate today?", "How's it going?", "Give me a tip for studying physics.", "I'm a bit tired.",
        "What should I do first?", "Tell me something interesting.", "Thanks, that helped.", "Can you remind me what matters this week?",
        "I think I did badly on that test.", "Say something to start my day."]


def count(texts: list[str]) -> dict:
    texts = [t for t in texts if t]
    banned = sum(BANNED in t.lower() for t in texts)
    name = r"(,\s*isaac\b(?!')|\bisaac\s*[,.!?]|^\s*isaac\b(?!'))"      # his name said TO him, not "Isaac's notes"
    addressed = [t for t in texts if re.search(r"\bboss\b", t, re.I) or re.search(name, t, re.I)]
    good = [t for t in addressed if re.search(r"\bboss\b", t, re.I) and not re.search(name, t, re.I)]
    return {"lines": len(texts), "banned": banned, "addressed": len(addressed), "boss": len(good)}


async def run(n: int):
    from misty_engine import brief, chat, follows, ways
    from misty_engine.openrouter import user_approved
    ctx, home = _harness.temp_ctx(copy_live=True)
    try:
        ways.set_way(ctx, "address", "Call him boss.", "address", "boss")
        ways.set_way(ctx, "tone", "Dry and understated, not cheerful.", "tone")
        ways.set_way(ctx, "no good morning", "Never open with 'good morning'.", "never_say")
        saved = [json.loads(r["facts"]) for r in ctx.con.execute("SELECT facts FROM briefs ORDER BY at DESC LIMIT 10")]
        facts_list = saved or [FALLBACK_FACTS]
        sem = asyncio.Semaphore(4)
        usd = [0.0]

        async def guarded(coro):
            async with sem:
                try:
                    return await coro
                except Exception as e:                      # a failed call is a missing line, never a crash
                    print("  (call failed:", type(e).__name__, str(e)[:80], ")", file=sys.stderr)
                    return None

        async def one_brief(i):
            f = dict(facts_list[i % len(facts_list)])
            f.pop("openable", None)
            r = await brief.compose(ctx, {"facts": f, "openable": []})
            return r["say"]

        async def one_follow(i):
            text, items = FOLLOW_CASES[i % len(FOLLOW_CASES)]
            _w, say, _i, _when = await follows.judge(ctx, {"text": text}, items, baseline=False)
            return say

        async def one_chat(i, voice):
            m = ctx.pinned.models
            body = {"model": m.haiku, "messages": chat.messages(ctx, CHAT[i % len(CHAT)], voice=voice), "max_tokens": 300,
                    "temperature": 0.3, "provider": {"order": list(m.haiku_provider_order), "allow_fallbacks": True},
                    "reasoning": dict(m.haiku_reasoning), "tools": chat.TOOLS, "tool_choice": "none"}
            with user_approved():
                d = await ctx.openrouter.post(ctx.pinned.openrouter.chat_path, body, job_id=None, phase="live", build_id=None,
                                              estimate_usd=0.01, model=m.haiku, purpose="chat")
            usd[0] += float((d.get("usage") or {}).get("cost") or 0.0)
            return d["choices"][0]["message"].get("content") or ""

        before = ctx.spend.spent_purpose("brief", "live") + ctx.spend.spent_purpose("follow", "live")
        out = {
            "brief": await asyncio.gather(*(guarded(one_brief(i)) for i in range(n))),
            "follow": await asyncio.gather(*(guarded(one_follow(i)) for i in range(n))),
            "chat": await asyncio.gather(*(guarded(one_chat(i, False)) for i in range(n))),
            "voice": await asyncio.gather(*(guarded(one_chat(i, True)) for i in range(n))),
        }
        usd[0] += ctx.spend.spent_purpose("brief", "live") + ctx.spend.spent_purpose("follow", "live") - before
        return out, usd[0], len(saved)
    finally:
        _harness.cleanup(home)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    a = ap.parse_args()
    out, usd, n_saved = asyncio.run(run(a.n))
    banned_total, ok = 0, True
    print(f"ways surfaces (briefs built from {n_saved or 'fallback'} saved facts), ${usd:.4f}")
    for surface, texts in out.items():
        c = count(texts)
        banned_total += c["banned"]
        follows_way = c["addressed"] == 0 or c["boss"] / c["addressed"] >= 0.9
        ok &= follows_way and c["lines"] == a.n
        print(f"  {surface:7} lines {c['lines']}/{a.n}, 'good morning' in {c['banned']}, addressed him in {c['addressed']}, "
              f"used 'boss' in {c['boss']}{'' if follows_way else '  <- MISS'}")
        for t in texts:
            if t and (BANNED in t.lower() or re.search(r"\bisaac\b(?!')", t, re.I)):
                print("    BAD:", t[:140])
    print(f"banned phrase: {banned_total} of {sum(len([t for t in v if t]) for v in out.values())}")
    ok &= banned_total == 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
