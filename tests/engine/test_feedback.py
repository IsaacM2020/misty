"""Feedback (Season 3, Day 6): every line she tells him on her own is logged, and all four kinds of reaction are
captured: a thumb, being ignored, "you should have told me", "that's too much". Each miss keeps what she knew then."""
import json

import pytest

from misty_engine import chat, feedback, gate
from misty_engine.gate import Moment
from test_chat import ScriptBrain, call, say
from test_gate import M, T, g  # noqa: F401  (the fixture: a ctx at a quiet afternoon with the app watching)


async def tell(ctx, text="Cricinfo reports play starts at 3pm.", key="k1", u="soon"):
    await gate.submit(ctx, Moment(text, u, topic="f1", topic_text="India vs NZ", source_name="Cricinfo", key=key))
    ctx.clock.t += gate.MIN_GAP_S + 1
    gate.set_presence(ctx, 300.0)
    return ctx.con.execute("SELECT id FROM told ORDER BY at DESC LIMIT 1").fetchone()[0]


@pytest.mark.asyncio
async def test_every_line_she_tells_him_is_logged_with_what_the_gate_saw(g):
    await tell(g)
    r = g.con.execute("SELECT * FROM told").fetchone()
    seen = json.loads(r["seen"])
    assert r["said"] and r["why"] and seen["idle_s"] == 90.0 and seen["hour"] == 15 and seen["in_session"] is False


@pytest.mark.asyncio
async def test_a_thumbs_down_is_saved_as_a_mistake_with_what_she_knew(g):
    g.con.execute("INSERT INTO cares(id,key,kind,text,weight,status,created_at,updated_at) VALUES ('c1','cricket','interest','loves cricket',3,'active',0,0)")
    tid = await tell(g)
    assert feedback.rate(g, tid, "down", "not useful") == {"ok": True, "value": "down", "line": "Cricinfo reports play starts at 3pm."}
    assert g.con.execute("SELECT feedback FROM told").fetchone()[0] == "down"
    m = g.con.execute("SELECT * FROM mistakes").fetchone()
    snap = json.loads(m["snapshot"])
    assert m["kind"] == "down" and m["told_id"] == tid and m["note"] == "not useful"
    assert snap["line"]["why"] and snap["line"]["seen"]["hour"] == 15 and "cricket (matters a lot)" in snap["cares"]


@pytest.mark.asyncio
async def test_a_thumbs_up_is_kept_and_changing_your_mind_replaces_the_mistake(g):
    tid = await tell(g)
    feedback.rate(g, tid, "down")
    assert g.con.execute("SELECT count(*) FROM mistakes").fetchone()[0] == 1
    feedback.rate(g, tid, "up")
    assert g.con.execute("SELECT feedback FROM told").fetchone()[0] == "up"
    assert g.con.execute("SELECT count(*) FROM mistakes").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_bad_thumbs_are_refused(g):
    assert feedback.rate(g, "td_nope", "up")["ok"] is False
    tid = await tell(g)
    assert feedback.rate(g, tid, "meh")["ok"] is False


@pytest.mark.asyncio
async def test_a_line_nobody_reacts_to_becomes_a_soft_thumbs_down_after_three_hours(g):
    tid = await tell(g)
    assert feedback.sweep_ignored(g) == 0
    g.clock.t += feedback.IGNORE_AFTER_S + 10
    assert feedback.sweep_ignored(g) == 1
    assert feedback.sweep_ignored(g) == 0                               # only once
    assert g.con.execute("SELECT feedback FROM told").fetchone()[0] == "ignored"
    m = g.con.execute("SELECT * FROM mistakes").fetchone()
    assert m["kind"] == "ignored" and m["told_id"] == tid


@pytest.mark.asyncio
async def test_a_line_he_rated_is_not_ignored(g):
    tid = await tell(g)
    feedback.rate(g, tid, "up")
    g.clock.t += feedback.IGNORE_AFTER_S + 10
    assert feedback.sweep_ignored(g) == 0


@pytest.mark.asyncio
async def test_two_ignored_lines_back_off_the_topic(g):
    await tell(g, key="a")
    await tell(g, key="b")
    g.clock.t += feedback.IGNORE_AFTER_S + 10
    gate.set_presence(g, 300.0)
    feedback.sweep_ignored(g)
    d = await gate.submit(g, Moment("more", "now", topic="f1", key="c"))
    assert d.action == "wait" and d.why == "backing off this topic"


@pytest.mark.asyncio
async def test_you_should_have_told_me_is_saved_with_what_she_knew_and_what_was_waiting(g):
    await tell(g)
    r = feedback.record(g, "missed", "the Lakers game was moved to Friday", "you should've told me about the Lakers")
    assert r == {"ok": True, "kind": "missed", "about": "the Lakers game was moved to Friday"}
    m = g.con.execute("SELECT * FROM mistakes WHERE kind='missed'").fetchone()
    snap = json.loads(m["snapshot"])
    assert m["told_id"] is None and "Lakers" in m["note"] and snap["seen"]["hour"] == 15 and "recent_told" in snap


def test_missed_needs_to_say_about_what(g):
    assert feedback.record(g, "missed", "")["ok"] is False


@pytest.mark.asyncio
async def test_thats_too_much_is_saved_and_returns_the_current_limit_so_she_can_lower_it(g):
    await tell(g)
    r = feedback.record(g, "too_much", note="that's too much")
    assert r["ok"] and r["daily_limit"] == 5 and r["told_today"] == 1
    m = g.con.execute("SELECT * FROM mistakes WHERE kind='too_much'").fetchone()
    assert m["note"] == "that's too much" and m["told_id"] is not None      # tied to the last thing she said


@pytest.mark.asyncio
async def test_a_thumb_by_chat_goes_to_the_latest_line_and_with_none_it_says_so(g):
    assert feedback.record(g, "up")["ok"] is False
    tid = await tell(g)
    assert feedback.record(g, "up")["ok"]
    assert g.con.execute("SELECT feedback FROM told WHERE id=?", (tid,)).fetchone()[0] == "up"
    g.clock.t += feedback.RECENT_S + 1
    assert feedback.record(g, "down")["ok"] is False                      # too long ago to be what he means


@pytest.mark.asyncio
async def test_the_recent_lines_are_in_the_prompt_so_a_reaction_has_something_to_attach_to(g):
    assert "Things you told him on your own lately" not in "".join(chat.system_parts(g, "nice"))
    tid = await tell(g)
    _, changing = chat.system_parts(g, "nice")
    assert "Cricinfo reports play starts at 3pm." in changing and "(Cricinfo)" in changing
    feedback.rate(g, tid, "up")
    assert "[he gave it: up]" in chat.system_parts(g, "x")[1]
    fixed, changing_e = chat.system_parts(g, "x", exam=True)
    assert "call feedback" not in fixed and "Things you told him on your own lately" not in changing_e


@pytest.mark.asyncio
async def test_chat_calls_feedback_for_a_reaction(g):
    tid = await tell(g)
    g.openrouter = ScriptBrain({"calls": [call("feedback", kind="down", note="not useful")]}, {"content": "Got it, noted that one was not useful."})
    out, _, _ = await say(g, "that wasn't useful")
    assert out["actions"][0]["tool"] == "feedback" and out["actions"][0]["ok"]
    assert g.con.execute("SELECT feedback FROM told WHERE id=?", (tid,)).fetchone()[0] == "down"


@pytest.mark.asyncio
async def test_the_rpcs_list_and_rate(g):
    from misty_engine.api import build_methods
    m = build_methods(g)
    tid = await tell(g)
    assert (await m["told.rate"]({"id": tid, "value": "up"}))["ok"]
    r = await m["told.list"]({})
    assert r["told"][0]["feedback"] == "up" and r["told"][0]["said"]
    assert (await m["told.rate"]({"id": tid, "value": "sideways"}))["ok"] is False


# ---- F4: talking over a line she began herself -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_talking_over_a_line_marks_it_interrupted_not_down(g):
    tid = await tell(g)
    r = feedback.interrupted(g, tid)
    assert r == {"ok": True, "value": "interrupted"}
    assert g.con.execute("SELECT feedback FROM told").fetchone()[0] == "interrupted"
    assert g.con.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0] == 0           # not a thumbs down by itself
    assert feedback.sweep_ignored(g) == 0                                              # and never swept as ignored later
    g.clock.t += feedback.IGNORE_AFTER_S + 1
    assert feedback.sweep_ignored(g) == 0


@pytest.mark.asyncio
async def test_an_interrupt_never_overwrites_a_thumb_and_a_thumb_overwrites_it(g):
    tid = await tell(g)
    feedback.rate(g, tid, "up")
    assert feedback.interrupted(g, tid)["ok"] is True
    assert g.con.execute("SELECT feedback FROM told").fetchone()[0] == "up"
    tid2 = await tell(g, key="k2")
    feedback.interrupted(g, tid2)
    feedback.rate(g, tid2, "down")
    assert g.con.execute("SELECT feedback FROM told WHERE id=?", (tid2,)).fetchone()[0] == "down"


@pytest.mark.asyncio
async def test_interrupting_an_unknown_line_or_a_note_is_harmless(g):
    assert feedback.interrupted(g, "nope")["ok"] is False
    assert feedback.interrupted(g, "note")["ok"] is False
    assert feedback.interrupted(g, "")["ok"] is False
