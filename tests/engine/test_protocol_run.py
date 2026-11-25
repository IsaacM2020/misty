"""D7: a trigger runs the steps, in order, through the move registry. Match set 20 and 20, stop on failure, a refused ask
stops the rest, repeat-safe after a crash, end steps, the fast path in chat.send."""
import json
from pathlib import Path

import pytest
import yaml

from misty_engine import chat, moves, protocols
from misty_engine.moves import registry, testing
from misty_engine.moves.base import Move, Outcome, Param
from test_chat import ScriptBrain, say

FIX = yaml.safe_load((Path(__file__).parent / "fixtures" / "protocol_match.yaml").read_text())
def S(move, **p):
    return {"move": move, "params": p}


@pytest.fixture
def mv():
    saved = dict(registry.MOVES)
    log = []

    async def run(ctx, p): log.append(p); return Outcome(True, what="did it")
    async def okv(ctx, p, o): return True
    async def boom(ctx, p): raise RuntimeError("x")
    for n in ("p_a", "p_b", "p_c"):
        registry.register(Move(n, "t", {"n": Param(int, required=False)}, "safe", run, okv, says=("a", "b", "c"), not_mine=(("a", "do"), ("b", "do"))))
    registry.register(Move("p_bad", "t", {}, "safe", boom, says=("a", "b", "c"), not_mine=(("a", "do"), ("b", "do"))))
    registry.register(Move("p_ask", "t", {}, "ask", run, okv, preview=lambda p: "ok?", says=("a", "b", "c"), not_mine=(("a", "do"), ("b", "do"))))
    yield log
    registry.MOVES.clear(); registry.MOVES.update(saved)


def seed_match_set(ctx):
    for p in FIX["protocols"]:
        protocols.save(ctx, p["key"], p["triggers"], [S("open_app", name="Notes")], [S("focus", state="off")] if p.get("end") else None)


def test_match_set_20_of_20_and_no_near_miss_matches(ctx):
    seed_match_set(ctx)
    ctx.embedder = None                                   # the word tiers alone must hold the whole set
    for say_, want in FIX["matches"]:
        r = protocols.match(ctx, say_)
        assert r is not None and r["key"] == want, say_
    for say_ in FIX["misses"]:
        assert protocols.match(ctx, say_) is None, say_
    assert len(FIX["matches"]) == 20 and len(FIX["misses"]) == 20


@pytest.mark.asyncio
async def test_steps_run_in_order_with_their_own_call_ids_and_the_run_id_in_the_log(ctx, mv):
    ctx.settings.set("shortcut_allow", [])
    protocols.save(ctx, "go", ["go time"], [S("p_a", n=1), S("p_b", n=2), S("p_c", n=3)])
    p = protocols.get(ctx.con, "go")
    async def st(s): ...
    r = await protocols.run(ctx, p, None, st)
    assert r["ok"] and len(r["done"]) == 3 and r["failed"] is None
    assert [x["n"] for x in mv] == [1, 2, 3]
    rows = ctx.con.execute("SELECT call_id, protocol FROM moves_log ORDER BY id").fetchall()
    assert [x["call_id"] for x in rows] == [f"{r['run_id']}-{i}" for i in (1, 2, 3)] and {x["protocol"] for x in rows} == {r["run_id"]}
    p2 = protocols.get(ctx.con, "go")
    assert p2["runs"] == 1 and p2["last_run_at"] is not None


@pytest.mark.asyncio
async def test_it_stops_at_the_first_failure_and_names_the_step(ctx, mv):
    protocols.save(ctx, "go", ["go time"], [S("p_a", n=1), S("p_bad"), S("p_c", n=3)])
    async def st(s): ...
    r = await protocols.run(ctx, protocols.get(ctx.con, "go"), None, st)
    assert not r["ok"] and r["failed"]["step"] == 2 and r["failed"]["move"] == "p_bad" and len(mv) == 1 and r["done"] == ["p a (n 1)"]


@pytest.mark.asyncio
async def test_a_refused_ask_step_stops_the_rest_and_a_yes_goes_on(ctx, mv):
    protocols.save(ctx, "go", ["go time"], [S("p_a", n=1), S("p_ask"), S("p_c", n=3)])
    async def st(s): ...
    async def no(q): return False
    async def yes(q): return True
    r = await protocols.run(ctx, protocols.get(ctx.con, "go"), no, st)
    assert r["failed"]["step"] == 2 and r["failed"]["declined"] and [x.get("n") for x in mv] == [1]
    r2 = await protocols.run(ctx, protocols.get(ctx.con, "go"), yes, st)
    assert r2["ok"] and len(r2["done"]) == 3


@pytest.mark.asyncio
async def test_a_retry_after_a_crash_mid_run_does_not_do_a_step_twice(ctx, mv):
    protocols.save(ctx, "go", ["go time"], [S("p_a", n=1), S("p_b", n=2)])
    p = protocols.get(ctx.con, "go")
    async def st(s): ...
    await moves.run(ctx, "p_a", {"n": 1}, "runX-1", None, "him", protocol="runX")           # step 1 happened, then the engine died
    r = await protocols.run(ctx, p, None, st, run_id="runX")
    assert r["ok"] and [x["n"] for x in mv] == [1, 2]                                        # step 1 was not run again


@pytest.mark.asyncio
async def test_end_steps_run_for_the_protocol_that_ran_today(ctx, mv):
    protocols.save(ctx, "math mode", ["math mode"], [S("p_a", n=1)], [S("p_b", n=9)])
    assert protocols.match_end(ctx, "I'm done") is None                                      # nothing ran today yet
    p = protocols.get(ctx.con, "math mode")
    async def st(s): ...
    await protocols.run(ctx, p, None, st)
    assert protocols.match_end(ctx, "I'm done.")["key"] == "math mode"
    assert protocols.match_end(ctx, "stop math mode")["key"] == "math mode"
    assert protocols.match_end(ctx, "I'm done with chem") is None
    r = await protocols.run(ctx, p, None, st, steps_key="end_steps")
    assert r["ok"] and mv[-1]["n"] == 9


@pytest.mark.asyncio
async def test_the_trigger_runs_before_the_model_and_the_first_step_is_fast(ctx, mv, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="misty")
    protocols.save(ctx, "go", ["go time"], [S("p_a", n=1), S("p_b", n=2)])
    brain = ScriptBrain({"content": "Done, both steps ran."})
    ctx.openrouter = brain
    out, spoken, _ = await say(ctx, "go time")
    assert [x["n"] for x in mv] == [1, 2] and out["actions"][0] == {"tool": "protocol", "ok": True}
    msgs = brain.bodies[0]["messages"]
    assert any(m.get("tool_calls") and m["tool_calls"][0]["function"]["name"] == "protocol" for m in msgs)
    assert "p a" in next(m["content"] for m in msgs if m.get("role") == "tool")
    assert len(brain.bodies) == 1                                                            # one model call: only the sentence
    ms = [r.misty_fields["ms"] for r in caplog.records if r.getMessage() == "PROTOCOL_FIRST_STEP"]
    assert ms and ms[0] < 300                                                                # the first step starts within 300 ms (logged)


@pytest.mark.asyncio
async def test_a_long_message_goes_to_the_model_not_the_fast_path(ctx, mv):
    protocols.save(ctx, "go", ["go time"], [S("p_a", n=1)])
    ctx.openrouter = ScriptBrain({"content": "ok"})
    await say(ctx, "what does go time do exactly when I say it")
    assert mv == []


@pytest.mark.asyncio
async def test_a_failure_is_in_the_tool_result_so_she_names_the_step(ctx, mv):
    protocols.save(ctx, "go", ["go time"], [S("p_bad")])
    brain = ScriptBrain({"content": "The first step failed."})
    ctx.openrouter = brain
    await say(ctx, "go time")
    tool = next(m["content"] for m in brain.bodies[0]["messages"] if m.get("role") == "tool")
    assert '"failed"' in tool and "p_bad" in tool
