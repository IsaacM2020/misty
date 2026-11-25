#!/usr/bin/env python3
"""Card F1 check: does a warm on the key press make the next real turn hit the provider's prompt cache?

    engine/.venv/bin/python scripts/checks/warm_check.py --starts 10 --max-usd 0.04     # PAID, pending: about $0.002 a start
    engine/.venv/bin/python scripts/checks/warm_check.py --dry                         # free: a fake model, no waiting

One "cold start" = a fresh engine context on a throwaway COPY of the live db (so the prompt is the real one), a wait long
enough for the provider's cache to expire (default 360 s, it lives about 5 minutes), then `chat.warm`, then one typed plain
message sent as a voice turn. Pass: the real turn's `cached` token count is above 0 in at least 90 percent of the starts
(9 of 10). It also prints how many starts really were cold (the warm itself saw cached=0), the warm's cost, and the
real turn's first word (engine marks, ms from receipt) so `talk_baseline.py` numbers have a partner.
Needs the real key (keychain) and paid calls; the live ~/Misty is only read (copied), never written.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PHRASES = ["what is a tide", "tell me a joke", "how are you today", "what does a thermostat do", "say something kind",
           "what is a good name for a cat", "explain a rainbow in one line", "what is two plus two", "how do bees make honey",
           "what is a good snack"]
BAR = 0.9


async def one_start(ctx, phrase: str, gap: float, sleep) -> dict:
    """One cold start on `ctx`: wait, warm, then a real typed turn. Returns the numbers, never raises for a model failure."""
    from misty_engine import chat
    from misty_engine.openrouter import user_approved
    from misty_engine.turn_timing import Marks
    import json

    async def nothing(_):
        return None

    async def no(_):
        return False
    await sleep(gap)
    w = await chat.warm(ctx, voice=True)
    marks = Marks()
    cached, err, turn_usd = 0, "", 0.0
    try:
        with user_approved():
            out = await chat.send(ctx, phrase, on_delta=nothing, on_status=nothing, confirm=no, voice=True, marks=marks)
        turn_usd = float(out.get("usd") or 0.0)
        row = ctx.con.execute("SELECT meta FROM chat_messages WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()
        rounds = (json.loads(row["meta"] or "{}").get("rounds") or []) if row else []
        cached = int(rounds[0]["cached"]) if rounds else 0
    except Exception as e:                                  # a failed turn counts as a miss, the run goes on
        err = type(e).__name__
    items = dict(marks.items)
    return {"warm": w, "warm_usd": float(w.get("usd") or 0.0), "turn_usd": turn_usd, "cached": cached,
            "first_ms": items.get("first_delta_out"), "err": err}


async def run_starts(make_ctx, starts: int, gap: float, max_usd: float, sleep=asyncio.sleep) -> list[dict]:
    rows, spent = [], 0.0
    for i in range(starts):
        if spent >= max_usd:
            print(f"stopped at the money cap after {len(rows)} starts (${spent:.4f})")
            break
        ctx, cleanup = make_ctx()
        try:
            r = await one_start(ctx, PHRASES[i % len(PHRASES)], gap, sleep)
        finally:
            cleanup()
        spent += r["warm_usd"] + r["turn_usd"]
        rows.append(r)
        print(f"  start {i + 1}: warm={r['warm'].get('kind')}/{r['warm'].get('why', 'ok')} warm_cached={r['warm'].get('cached', 0)} "
              f"turn_cached={r['cached']} first_word_ms={r['first_ms']} usd={r['warm_usd'] + r['turn_usd']:.5f}"
              + (f" ERR={r['err']}" if r["err"] else ""), flush=True)
    return rows


def summary(rows: list[dict]) -> tuple[str, bool]:
    n = len(rows)
    if not n:
        return "no starts ran", False
    hit = sum(1 for r in rows if r["cached"] > 0)
    need = math.ceil(BAR * n)
    cold = sum(1 for r in rows if r["warm"].get("kind") == "sent" and r["warm"].get("cached", 0) == 0)
    firsts = [r["first_ms"] for r in rows if r["first_ms"] is not None]
    usd = sum(r["warm_usd"] + r["turn_usd"] for r in rows)
    warm_usd = sum(r["warm_usd"] for r in rows)
    ok = hit >= need
    lines = [f"cache hit on the real turn: {hit} of {n} (need {need})",
             f"really cold (the warm itself had cached=0): {cold} of {n}" + ("" if cold >= n - 1 else "  <- the wait was too short for some"),
             f"first word, engine marks, median: {statistics.median(firsts):.0f} ms" if firsts else "first word: no data",
             f"cost: ${usd:.4f} for {n} starts, of which warming ${warm_usd:.4f} (${warm_usd / n:.5f} a start; a day of 20 warms about ${20 * warm_usd / n:.3f})",
             "PASS" if ok else "FAIL"]
    return "\n".join(lines), ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--starts", type=int, default=10)
    ap.add_argument("--gap", type=float, default=360.0, help="seconds to wait before each start so the cache has expired")
    ap.add_argument("--max-usd", type=float, default=0.04)
    ap.add_argument("--dry", action="store_true", help="free: a fake model, no waiting")
    a = ap.parse_args(argv)
    import _harness

    if a.dry:
        from fake_model import FakeCachingBrain

        def make():
            ctx, home = _harness.temp_ctx(copy_live=False)
            ctx.openrouter = FakeCachingBrain()
            return ctx, lambda: _harness.cleanup(home)
        gap = 0.0
    else:
        def make():
            ctx, home = _harness.temp_ctx(copy_live=True)
            ctx.settings.set("cap_daily_usd", 5.0)
            return ctx, lambda: _harness.cleanup(home)
        gap = a.gap
    rows = asyncio.run(run_starts(make, a.starts, gap, a.max_usd))
    text, ok = summary(rows)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
