"""D3: the yes. Yes, no, timeout, two asks in one turn, a model that claims success after a no is caught by the result."""
import asyncio
import json

import pytest

from misty_engine import moves
from misty_engine.moves import registry, testing


@pytest.fixture(autouse=True)
def tw():
    saved = dict(registry.MOVES)
    testing.register_test_write()
    yield
    registry.MOVES.clear(); registry.MOVES.update(saved)


def answers(*seq):
    asked = []
    it = iter(seq)

    async def confirm(q):
        asked.append(q)
        return next(it)
    return confirm, asked


@pytest.mark.asyncio
async def test_yes_runs_it_and_logs_yes(ctx):
    c, asked = answers(True)
    r = await moves.run(ctx, "test_write", {"what": "a"}, "y1", c)
    assert r["ok"] and r["verified"] and testing.rows(ctx) == ["a"]
    assert asked == ['Write "a" to the test table?']                # the preview is the move's own sentence, built in code
    row = ctx.con.execute("SELECT * FROM moves_log WHERE call_id='y1'").fetchone()
    assert row["answer"] == "yes" and row["asked"] == 1


@pytest.mark.asyncio
async def test_no_writes_nothing_and_says_what_to_say(ctx):
    c, _ = answers(False)
    r = await moves.run(ctx, "test_write", {"what": "a"}, "n1", c)
    assert r["declined"] and not r["ok"] and not r["verified"] and r["say"] == "Okay, not written." and testing.rows(ctx) == []
    assert ctx.con.execute("SELECT answer FROM moves_log WHERE call_id='n1'").fetchone()[0] == "no"


@pytest.mark.asyncio
async def test_silence_is_a_no_after_the_limit(ctx, monkeypatch):
    monkeypatch.setattr(registry, "CONFIRM_SECONDS", 0.05)

    async def never(q):
        await asyncio.sleep(5)
        return True
    r = await moves.run(ctx, "test_write", {"what": "a"}, "t1", never)
    assert r["declined"] and r["timed_out"] and testing.rows(ctx) == []
    assert ctx.con.execute("SELECT answer FROM moves_log WHERE call_id='t1'").fetchone()[0] == "timeout"


def test_the_limit_is_a_minute():
    assert registry.CONFIRM_SECONDS == 60


@pytest.mark.asyncio
async def test_two_asks_in_one_turn_each_get_their_own_yes(ctx):
    c, asked = answers(True, False)
    a = await moves.run(ctx, "test_write", {"what": "a"}, "m1", c)
    b = await moves.run(ctx, "test_write", {"what": "b"}, "m2", c)
    assert a["ok"] and b["declined"] and len(asked) == 2 and testing.rows(ctx) == ["a"]    # one yes never covers two things


@pytest.mark.asyncio
async def test_no_way_to_ask_means_no(ctx):
    r = await moves.run(ctx, "test_write", {"what": "a"}, "x1", None)
    assert not r["ok"] and testing.rows(ctx) == []


@pytest.mark.asyncio
async def test_a_repeat_after_a_no_does_not_write_and_does_not_claim_success(ctx):
    c, _ = answers(False, True)
    await moves.run(ctx, "test_write", {"what": "a"}, "r1", c)
    again = await moves.run(ctx, "test_write", {"what": "a"}, "r1", c)             # a retry of the declined call
    assert not again["verified"] and testing.rows(ctx) == []


@pytest.mark.asyncio
async def test_text_she_read_is_called_out_in_the_preview(ctx):
    c, asked = answers(True)
    await moves.run(ctx, "test_write", {"what": "a"}, "s1", c, source="text")
    assert "not from him" in asked[0]


@pytest.mark.asyncio
async def test_a_model_that_claims_success_after_a_no_is_caught_by_the_result(ctx):
    """The tool result is what she is told: after a no it says declined, not verified, and how to word it."""
    c, _ = answers(False)
    r = await moves.run(ctx, "test_write", {"what": "a"}, "c1", c)
    assert r["verified"] is False and r["ok"] is False and "never say it was done" in r["error"]


def test_every_ask_move_has_a_preview_in_code(ctx):
    for m in registry.MOVES.values():
        if m.risk == "ask":
            assert callable(m.preview)
            args = {k: ("x" if p.kind is str else True if p.kind is bool else 1) for k, p in m.params.items()}
            assert isinstance(m.preview(args, ctx) if getattr(m.preview, "wants_ctx", False) else m.preview(args), str)


def test_test_write_is_not_in_the_models_catalog():
    saved = dict(registry.MOVES)
    registry.MOVES.pop("test_write")
    assert "test_write" not in registry.catalog()
    registry.MOVES.update(saved)
