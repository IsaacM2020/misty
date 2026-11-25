"""D9: routines. Each trigger kind, the automatic rule refused when a step asks, the gate holds a routine in class, a stale one is
dropped (never run late), each trigger fires once, and "yes" runs the offered routine."""
from datetime import datetime

import pytest

from misty_engine import chat, gate, protocols, timers
from misty_engine.clock import SGT
from misty_engine.errors import Fatal
from misty_engine.moves import registry
from misty_engine.moves.base import Move, Outcome, Param


def at(h, m=0, day=9):
    return datetime(2026, 11, day, h, m, tzinfo=SGT).timestamp()          # day 9 is a Monday


def S(move, **p):
    return {"move": move, "params": p}


@pytest.fixture
def mv():
    saved = dict(registry.MOVES)
    ran = []

    async def run(ctx, p): ran.append(p); return Outcome(True, what="did it")
    async def okv(ctx, p, o): return True
    registry.register(Move("r_safe", "t", {"n": Param(int, required=False)}, "safe", run, okv, says=("a", "b", "c"), not_mine=(("a", "do"), ("b", "do"))))
    registry.register(Move("r_ask", "t", {}, "ask", run, okv, preview=lambda p: "ok?", says=("a", "b", "c"), not_mine=(("a", "do"), ("b", "do"))))
    yield ran
    registry.MOVES.clear(); registry.MOVES.update(saved)


def routine(ctx, when, key="after class", steps=None, auto=None, trig=()):
    return protocols.save(ctx, key, list(trig), steps or [S("r_safe", n=1)], None, when, auto)


def told(ctx):
    return [r["text"] for r in ctx.con.execute("SELECT text FROM told ORDER BY at")]


def present(ctx, idle=60.0):
    gate.set_presence(ctx, idle)


def end_class(ctx, subject="Chem", ended=None):
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, last_heard_at, status, ended_at) VALUES (?,?,?,?,?,?,?)",
                    (f"cs_{subject}{ended}", subject, subject, ended - 3000, ended, "ended", ended))


# ---- saving
def test_when_kinds_are_checked(ctx, mv):
    assert routine(ctx, {"kind": "time", "at": "7:30", "days": "weekdays"}, "a")["when"] == {"kind": "time", "at": "07:30", "days": ["mon", "tue", "wed", "thu", "fri"]}
    assert routine(ctx, {"kind": "first_open"}, "b")["when"]["kind"] == "first_open"
    assert routine(ctx, {"kind": "class_end", "subject": "Chem"}, "c")["when"]["subject"] == "Chem"
    assert routine(ctx, {"kind": "timer_end", "label": "Math"}, "d")["when"]["label"] == "math"
    for bad in ({"kind": "yearly"}, {"kind": "time", "at": "25:00"}, {"kind": "time", "at": "07:30", "days": "someday"}, {"kind": "timer_end"}, "soon"):
        with pytest.raises(Fatal):
            routine(ctx, bad, "x")


def test_a_timer_routine_may_not_restart_its_own_timer(ctx, mv):
    with pytest.raises(Fatal) as e:
        routine(ctx, {"kind": "timer_end", "label": "math"}, "loop", [S("timer_start", minutes=5, label="Math")])
    assert "forever" in str(e.value)


def test_automatic_only_when_every_step_is_safe(ctx, mv):
    assert routine(ctx, {"kind": "first_open"}, "ok", auto=True)["auto"] is True
    with pytest.raises(Fatal) as e:
        routine(ctx, {"kind": "first_open"}, "bad", [S("r_safe"), S("r_ask")], auto=True)
    assert "every step is safe" in str(e.value)
    with pytest.raises(Fatal):
        protocols.save(ctx, "spoken only", ["do it now"], [S("r_safe")], None, None, True)          # not a routine: nothing to automate


def test_a_new_step_that_asks_turns_an_old_just_do_it_off(ctx, mv):
    routine(ctx, {"kind": "first_open"}, "x", auto=True)
    r = routine(ctx, None, "x", [S("r_safe"), S("r_ask")])                                             # same key, new list, auto not repeated
    assert r["auto"] is False and "auto_off" in r and r["when"] == {"kind": "first_open"}


def test_a_routine_needs_a_trigger_or_a_when(ctx):
    with pytest.raises(Fatal):
        protocols.save(ctx, "x", [], [S("open_app", name="Notes")])
    assert protocols.save(ctx, "x", [], [S("open_app", name="Notes")], None, {"kind": "first_open"})["triggers"] == []


# ---- firing
@pytest.mark.asyncio
async def test_a_time_routine_asks_once_and_yes_runs_it(ctx, mv):
    routine(ctx, {"kind": "time", "at": "07:30", "days": "weekdays"})
    ctx.clock.t = at(7, 29); present(ctx)
    assert await protocols.fire_due(ctx) == 0                                                           # not yet
    ctx.clock.t = at(7, 31); present(ctx)
    assert await protocols.fire_due(ctx) == 1 and await protocols.fire_due(ctx) == 0                    # once
    assert told(ctx) == ["It's 07:30. Run your after class routine?"]
    assert protocols.match(ctx, "yes")["key"] == "after class" and protocols.match(ctx, "yes") is None  # the offer is used up


@pytest.mark.asyncio
async def test_a_time_routine_does_not_fire_on_the_wrong_day(ctx, mv):
    routine(ctx, {"kind": "time", "at": "07:30", "days": "weekdays"})
    ctx.clock.t = at(7, 31, day=14); present(ctx)                                                       # Saturday
    assert await protocols.fire_due(ctx) == 0


@pytest.mark.asyncio
async def test_first_open_fires_once_a_day_when_he_is_at_the_mac(ctx, mv):
    routine(ctx, {"kind": "first_open"}, "morning")
    ctx.clock.t = at(8, 0); present(ctx, 30.0)
    assert await protocols.fire_due(ctx) == 1
    ctx.clock.t = at(8, 30); present(ctx, 30.0)
    assert await protocols.fire_due(ctx) == 0
    ctx.clock.t = at(8, 0, day=10); present(ctx, 30.0)
    assert await protocols.fire_due(ctx) == 1                                                           # a new day


@pytest.mark.asyncio
async def test_class_end_waits_while_a_class_is_open_then_fires(ctx, mv):
    routine(ctx, {"kind": "class_end", "subject": "chem"})
    ctx.clock.t = at(15, 0)
    end_class(ctx, "Chem", ended=at(14, 55))
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, last_heard_at, status) VALUES ('cs_open','Math','Math',?,?,'live')", (at(14, 56), at(14, 59)))
    present(ctx)
    assert await protocols.fire_due(ctx) == 0 and told(ctx) == []                                       # the gate holds it in class
    ctx.con.execute("UPDATE class_sessions SET status='ended', ended_at=? WHERE id='cs_open'", (at(15, 5),))
    ctx.clock.t = at(15, 6); present(ctx)
    assert await protocols.fire_due(ctx) == 1 and told(ctx) == ["Class is over. Run your after class routine?"]


@pytest.mark.asyncio
async def test_class_end_for_another_subject_does_not_fire(ctx, mv):
    routine(ctx, {"kind": "class_end", "subject": "chem"})
    ctx.clock.t = at(15, 0); end_class(ctx, "Spanish", ended=at(14, 55)); present(ctx)
    assert await protocols.fire_due(ctx) == 0


@pytest.mark.asyncio
async def test_a_routine_that_waited_over_an_hour_is_dropped_not_run_late(ctx, mv):
    routine(ctx, {"kind": "class_end"}, auto=True)
    ctx.clock.t = at(16, 30); end_class(ctx, "Chem", ended=at(15, 0)); present(ctx)
    assert await protocols.fire_due(ctx) == 0 and mv == [] and told(ctx) == []
    assert ctx.con.execute("SELECT outcome FROM routine_fires").fetchone()[0] == "dropped"
    ctx.con.execute("UPDATE class_sessions SET ended_at=?", (at(16, 25),))
    assert await protocols.fire_due(ctx) == 0                                                           # already dealt with: never later


@pytest.mark.asyncio
async def test_one_waiting_in_the_gate_then_stale_is_dropped(ctx, mv):
    routine(ctx, {"kind": "class_end"})
    ctx.clock.t = at(15, 0); end_class(ctx, "Chem", ended=at(14, 50)); present(ctx, 2.0)               # he is typing: the gate waits
    assert await protocols.fire_due(ctx) == 0
    ctx.clock.t = at(16, 0); present(ctx, 2.0)
    assert await protocols.fire_due(ctx) == 0 and ctx.con.execute("SELECT outcome FROM routine_fires").fetchone()[0] == "dropped"


@pytest.mark.asyncio
async def test_an_automatic_routine_runs_its_steps_through_the_gate(ctx, mv):
    routine(ctx, {"kind": "first_open"}, "morning", [S("r_safe", n=1), S("r_safe", n=2)], auto=True)
    ctx.clock.t = at(8, 0); present(ctx, 30.0)
    assert await protocols.fire_due(ctx) == 1
    assert [x["n"] for x in mv] == [1, 2] and told(ctx) == ["Good to see you at the Mac. Running your morning routine."]
    assert ctx.con.execute("SELECT outcome FROM routine_fires").fetchone()[0] == "ran"
    assert await protocols.fire_due(ctx) == 0 and len(mv) == 2


@pytest.mark.asyncio
async def test_an_automatic_routine_in_class_does_not_run(ctx, mv):
    routine(ctx, {"kind": "first_open"}, "morning", auto=True)
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, last_heard_at, status) VALUES ('cs_open','Math','Math',?,?,'live')", (at(7, 50), at(7, 59)))
    ctx.clock.t = at(8, 0); present(ctx, 30.0)
    assert await protocols.fire_due(ctx) == 0 and mv == []


@pytest.mark.asyncio
async def test_an_old_just_do_it_never_rides_a_step_that_asks(ctx, mv):
    routine(ctx, {"kind": "first_open"}, "morning", auto=True)
    ctx.con.execute("UPDATE protocols SET steps=? WHERE key='morning'", ('[{"move": "r_ask", "params": {}}]',))      # changed behind its back
    ctx.clock.t = at(8, 0); present(ctx, 30.0)
    await protocols.fire_due(ctx)
    assert mv == [] and told(ctx) == ["Good to see you at the Mac. Run your morning routine?"]


@pytest.mark.asyncio
async def test_timer_end_fires_for_its_label_only(ctx, mv):
    routine(ctx, {"kind": "timer_end", "label": "math"}, "after math")
    ctx.clock.t = at(10, 0)
    timers.start(ctx, 1, "chem"); timers.start(ctx, 1, "math")
    ctx.clock.t = at(10, 2); present(ctx)
    await timers.fire_due(ctx)
    assert await protocols.fire_due(ctx) == 0                                                           # his timer line was just said: the gap holds it
    ctx.clock.t = at(10, 5); present(ctx)
    assert await protocols.fire_due(ctx) == 1
    assert [t for t in told(ctx) if "Run your after math" in t] == ["Your math timer is up. Run your after math routine?"]


@pytest.mark.asyncio
async def test_the_tool_saves_a_routine_and_a_routine_shows_in_plain_words(ctx, mv):
    async def st(s): ...
    async def cf(q): return True
    r = await chat.run_tool(ctx, "protocol", {"action": "save", "key": "after class", "steps": [S("r_safe", n=1)],
                                              "when": {"kind": "class_end"}, "auto": True}, on_status=st, confirm=cf)
    assert r["ok"] and r["auto"] and r["when"] == {"kind": "class_end", "subject": ""}
    s = await chat.run_tool(ctx, "protocol", {"action": "show"}, on_status=st, confirm=cf)
    assert s["protocols"][0]["when"] == "when a class ends" and s["protocols"][0]["auto"] is True
    assert "routine, when a class ends, runs without asking" in protocols.block(ctx.con)
