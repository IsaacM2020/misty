"""D8: "did for you" and undo. Only moves that stored an undo can be undone; undoing an ask move is shown first; both rows are in the log."""
import pytest

from misty_engine import chat, moves, protocols, timers
from misty_engine.moves import registry
from misty_engine.moves.base import Move, Outcome
from test_chat import ScriptBrain, call, say


async def st(s): ...
async def yes(q): return True


@pytest.mark.asyncio
async def test_undo_last_takes_back_a_timer_and_logs_both_rows(ctx):
    await moves.run(ctx, "timer_start", {"minutes": 25, "label": "maths"}, "u1")
    assert len(timers.active(ctx)) == 1
    r = await moves.undo(ctx, "last", None)
    assert r["ok"] and r["verified"] and r["undid"] == "timer_start" and not timers.active(ctx)
    names = [x["name"] for x in ctx.con.execute("SELECT name FROM moves_log ORDER BY id")]
    assert names == ["timer_start", "timer_stop"]
    assert not (await moves.undo(ctx, "last", None))["ok"]                                  # nothing left to take back


@pytest.mark.asyncio
async def test_a_move_without_an_undo_cannot_be_undone(ctx):
    async def run(c, p): return Outcome(True, what="done")
    async def ok(c, p, o): return True
    saved = dict(registry.MOVES)
    registry.register(Move("u_plain", "t", {}, "safe", run, ok, says=("a", "b", "c"), not_mine=(("a", "do"), ("b", "do"))))
    try:
        await moves.run(ctx, "u_plain", {}, "p1")
        rid = ctx.con.execute("SELECT id FROM moves_log WHERE call_id='p1'").fetchone()[0]
        r = await moves.undo(ctx, rid, None)
        assert not r["ok"] and "no way to undo" in r["error"]
        assert "nothing" in (await moves.undo(ctx, "last", None))["error"]
    finally:
        registry.MOVES.clear(); registry.MOVES.update(saved)


@pytest.mark.asyncio
async def test_undoing_an_ask_move_is_shown_first_and_a_no_leaves_it(ctx):
    done = []
    async def run(c, p): done.append(1); return Outcome(True, what="Added the thing", undo={"move": "timer_stop", "params": {"label": ""}})
    async def ok(c, p, o): return True
    saved = dict(registry.MOVES)
    registry.register(Move("u_ask", "t", {}, "ask", run, ok, preview=lambda p: "Add it?", says=("a", "b", "c"), not_mine=(("a", "do"), ("b", "do"))))
    asked = []
    async def conf(q):
        asked.append(q)
        return True
    async def deny(q):
        asked.append(q)
        return False
    try:
        await moves.run(ctx, "u_ask", {}, "a1", conf)
        asked.clear()
        r = await moves.undo(ctx, "last", deny)
        assert r["declined"] and asked[0].startswith("Take this back?") and "Added the thing" in asked[0]
        timers.start(ctx, 5, "")                                  # something for the undo (timer_stop) to stop
        r2 = await moves.undo(ctx, "last", conf)
        assert r2["ok"] and asked[1].startswith("Take this back?")
    finally:
        registry.MOVES.clear(); registry.MOVES.update(saved)


@pytest.mark.asyncio
async def test_the_same_undo_twice_runs_once(ctx):
    await moves.run(ctx, "timer_start", {"minutes": 5}, "t1")
    rid = ctx.con.execute("SELECT id FROM moves_log WHERE call_id='t1'").fetchone()[0]
    assert (await moves.undo(ctx, rid, None))["ok"]
    assert "already" in (await moves.undo(ctx, rid, None))["error"]
    assert not (await moves.undo(ctx, "banana", None))["ok"]


@pytest.mark.asyncio
async def test_recent_lists_newest_first_with_a_tick(ctx):
    await moves.run(ctx, "timer_start", {"minutes": 5, "label": "a"}, "r1")
    await moves.run(ctx, "windows_arrange", {"layout": "full", "apps": "Notes"}, "r2")    # fails: her hands are not connected
    rows = moves.recent(ctx)
    assert [r["name"] for r in rows] == ["windows_arrange", "timer_start"]
    assert rows[1]["verified"] is True and rows[1]["undoable"] and rows[0]["verified"] is False


@pytest.mark.asyncio
async def test_the_tool_answers_what_did_you_do_today_from_the_log_only(ctx):
    await moves.run(ctx, "timer_start", {"minutes": 5, "label": "a"}, "d1")
    r = await chat.run_tool(ctx, "move", {"action": "did"}, on_status=st, confirm=yes)
    assert [x["name"] for x in r["today"]] == ["timer_start"] and "only what is in it" in r["note"]
    ctx.clock.t += 2 * 86400
    assert (await chat.run_tool(ctx, "move", {"action": "did"}, on_status=st, confirm=yes))["today"] == []


@pytest.mark.asyncio
async def test_the_tool_undo_and_the_run_default(ctx):
    r = await chat.run_tool(ctx, "move", {"name": "timer_start", "params": {"minutes": 5}}, on_status=st, confirm=yes)
    assert r["ok"]
    u = await chat.run_tool(ctx, "move", {"action": "undo", "which": "last"}, on_status=st, confirm=yes)
    assert u["ok"] and u["undid"] == "timer_start"


@pytest.mark.asyncio
async def test_the_rpcs(ctx):
    from misty_engine.api import build_methods
    m = build_methods(ctx)
    await moves.run(ctx, "timer_start", {"minutes": 5}, "p1")
    assert (await m["moves.list"]({}))["moves"][0]["name"] == "timer_start"
    protocols.save(ctx, "go", ["go time"], [{"move": "open_app", "params": {"name": "Notes"}}])
    assert (await m["protocols.list"]({}))["protocols"][0]["key"] == "go"
