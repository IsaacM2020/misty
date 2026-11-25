"""F1: the paid warm_check script, run free with a fake caching model (no network, no waiting)."""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "checks"))
import warm_check as wc  # noqa: E402
from fake_model import FakeCachingBrain  # noqa: E402


def make_factory(ctx, seen_brain=None):
    brain = seen_brain or FakeCachingBrain()

    def make():
        ctx.openrouter = brain
        ctx.con.execute("DELETE FROM chat_messages")
        return ctx, lambda: None
    return make


async def no_sleep(_):
    return None


def test_a_warm_makes_the_real_turn_hit_the_cache(ctx):
    rows = []
    for i in range(3):
        ctx.clock.advance(400)
        rows += asyncio.run(wc.run_starts(make_factory(ctx), 1, 0, 1.0, no_sleep))
    assert [r["warm"]["kind"] for r in rows] == ["sent"] * 3
    assert rows[0]["warm"]["cached"] == 0 and rows[0]["cached"] > 0         # first start: the warm paid the miss, the real turn hit
    text, ok = wc.summary(rows)
    assert ok and "3 of 3" in text and "PASS" in text


def test_a_start_where_the_warm_was_skipped_misses_and_fails_the_bar(ctx):
    brain = FakeCachingBrain()
    # a real message a minute ago makes the warm skip (recent), so the real turn is the first call and misses
    from misty_engine import chat
    chat._save(ctx, "user", "earlier")
    ctx.clock.advance(60)

    def make():
        ctx.openrouter = brain
        return ctx, lambda: None
    rows = asyncio.run(wc.run_starts(make, 1, 0, 1.0, no_sleep))
    assert rows[0]["warm"] == {"kind": "skipped", "why": "recent"} and rows[0]["cached"] == 0
    text, ok = wc.summary(rows)
    assert not ok and "FAIL" in text


def test_the_bar_is_nine_of_ten():
    ok_row = {"warm": {"kind": "sent", "cached": 0}, "warm_usd": 0.001, "turn_usd": 0.0004, "cached": 5, "first_ms": 800, "err": ""}
    miss = {**ok_row, "cached": 0}
    assert wc.summary([ok_row] * 9 + [miss])[1] is True
    assert wc.summary([ok_row] * 8 + [miss] * 2)[1] is False
    assert wc.summary([])[1] is False


def test_the_money_cap_stops_the_run(ctx):
    rows = asyncio.run(wc.run_starts(make_factory(ctx), 5, 0, 0.0001, no_sleep))
    assert len(rows) == 1


def test_a_failing_model_is_a_miss_not_a_crash(ctx):
    class Boom:
        async def post(self, *a, **k):
            raise RuntimeError("down")

    def make():
        ctx.openrouter = Boom()
        return ctx, lambda: None
    rows = asyncio.run(wc.run_starts(make, 1, 0, 1.0, no_sleep))
    assert rows[0]["cached"] == 0 and rows[0]["err"] == "RuntimeError"


def test_the_summary_prints_the_cost_of_a_day_of_warms():
    row = {"warm": {"kind": "sent", "cached": 0}, "warm_usd": 0.002, "turn_usd": 0.0004, "cached": 5, "first_ms": 800, "err": ""}
    text, _ = wc.summary([row] * 10)
    assert "$0.002" in text and "20 warms" in text and "median: 800 ms" in text
