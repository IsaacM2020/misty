"""Ask the class: the right segments (with their neighbours) are found, made-up cites are dropped, an old class
answers from its notes and says the transcript is gone, and no match means no paid call."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from misty_engine import class_ask, classes
from misty_engine.runner import RunResult

DAY = 86400.0


class FakeRunner:
    def __init__(self, reply=None):
        self.reply, self.calls = reply, []

    async def run(self, system, parts, schema_name, **kw):
        self.calls.append({"parts": parts, "schema": schema_name, **kw})
        r = self.reply or {"answer": "He said a stress shifts it.", "cites": [2]}
        return RunResult(text=json.dumps(r), json=r, cost_usd=0.001)


@pytest.fixture
def ctx(con, clock, settings):
    return SimpleNamespace(con=con, clock=clock, settings=settings, runner=FakeRunner(), wiki=None, notify=None)


def chem(ctx, end=True):
    s = classes.start(ctx.con, ctx.clock, "Chem", None, None)["id"]
    for i, t in enumerate(["good morning", "today Le Chatelier principle", "a stress shifts the equilibrium",
                           "write that down", "now titrations"]):
        classes.add_segment(ctx.con, s, i * 10, i * 10 + 9, "room", t)
    if end:
        classes.end(ctx.con, ctx.clock, s)
    return s


def test_retrieve_finds_neighbours(ctx):
    s = chem(ctx)
    got = class_ask.retrieve(ctx.con, "what about chatelier?", sid=s)
    assert [g["seg"] for g in got] == [1, 2, 3]
    assert got[1]["text"] == "today Le Chatelier principle"


def test_ask_during_live_class_uses_segments_so_far(ctx):
    s = chem(ctx, end=False)
    r = asyncio.run(class_ask.ask(ctx, "what is a stress?", sid=s))
    assert r["answer"].startswith("He said")
    assert "a stress shifts the equilibrium" in "\n".join(ctx.runner.calls[0]["parts"])
    assert ctx.runner.calls[0]["purpose"] == "class" and ctx.runner.calls[0]["model_key"] == "haiku"


def test_ask_drops_made_up_cites(ctx):
    ctx.runner = FakeRunner({"answer": "x", "cites": [2, 99]})
    s = chem(ctx)
    r = asyncio.run(class_ask.ask(ctx, "stress equilibrium", sid=s))
    assert [c["seg"] for c in r["cites"]] == [2]
    assert r["cites"][0]["session"] == s and r["cites"][0]["t_start"] == 10


def test_ask_old_class_uses_notes_and_says_so(ctx):
    s = chem(ctx)
    ctx.con.execute("UPDATE class_sessions SET notes_json=?, status='noted' WHERE id=?",
                    (json.dumps({"summary": "Le Chatelier and titrations.", "sections": [], "remember": [],
                                 "action_items": [], "gaps": []}), s))
    ctx.clock.advance(200 * DAY)
    classes.prune(ctx.con, ctx.clock)
    ctx.runner = FakeRunner({"answer": "It covered Le Chatelier.", "cites": []})
    r = asyncio.run(class_ask.ask(ctx, "what was chatelier about", sid=s))
    assert r["answer"].startswith("The transcript for this class is gone (kept 6 months).")
    assert "Le Chatelier and titrations." in "\n".join(ctx.runner.calls[0]["parts"])


def test_no_hits_no_call(ctx):
    s = chem(ctx)
    r = asyncio.run(class_ask.ask(ctx, "photosynthesis?", sid=s))
    assert r == {"answer": "I couldn't find that in the class.", "cites": []}
    assert ctx.runner.calls == []


def test_across_classes_filters_by_subject(ctx):
    chem(ctx)
    m = classes.start(ctx.con, ctx.clock, "Physics", None, None)["id"]
    classes.add_segment(ctx.con, m, 0, 5, "room", "equilibrium of forces on a beam")
    got = class_ask.retrieve(ctx.con, "equilibrium", subject="Physics")
    assert {g["session"] for g in got} == {m}


def test_notes_for_chat_carries_the_debrief_parts(ctx):
    s = chem(ctx)
    notes = {"summary": "Equilibrium.", "sections": [], "remember": [], "action_items": [], "gaps": [],
             "lost": [{"text": "Asked why a catalyst does not move it", "seg": [2]}],
             "next_step": {"text": "Redo the catalyst question", "seg": [2]}}
    ctx.con.execute("UPDATE class_sessions SET status='noted', notes_json=? WHERE id=?", (json.dumps(notes), s))
    r = class_ask.notes_for_chat(ctx.con, "last")
    assert r["where_he_got_lost"] == "- Asked why a catalyst does not move it"
    assert r["next_step"] == "Redo the catalyst question"
    notes.pop("lost"); notes.pop("next_step")
    ctx.con.execute("UPDATE class_sessions SET notes_json=? WHERE id=?", (json.dumps(notes), s))
    r = class_ask.notes_for_chat(ctx.con, "last")
    assert r["where_he_got_lost"] == r["next_step"] == class_ask.NONE   # said outright, so nothing gets made up


def test_chat_prompt_tells_her_how_to_debrief():
    from misty_engine import chat
    assert "Debrief me" in chat.SYSTEM and "60 seconds" in chat.SYSTEM


def test_due_dates_say_how_far_off_they_are():
    from datetime import date
    today = date(2026, 11, 7)
    assert class_ask._when("2026-11-05", today) == "Thu 5 Nov, 2 days ago, already past"
    assert class_ask._when("2026-11-06", today) == "Fri 6 Nov, yesterday, already past"
    assert class_ask._when("2026-11-07", today) == "Sat 7 Nov, today"
    assert class_ask._when("2026-11-08", today) == "Sun 8 Nov, tomorrow"
    assert class_ask._when("2026-11-10", today) == "Tue 10 Nov, in 3 days"
    assert class_ask._when("2026-11-10", None) == "Tue 10 Nov"
    assert class_ask._when("next week", today) == "next week"           # not a date: passed through as written


def test_debrief_parts_come_before_the_long_notes(ctx):
    s = chem(ctx)
    notes = {"summary": "Equilibrium. " * 800, "sections": [], "remember": [], "action_items": [], "gaps": [],
             "lost": [{"text": "Asked why a catalyst does not move it", "seg": [2]}],
             "next_step": {"text": "Redo the catalyst question", "seg": [2]}}
    ctx.con.execute("UPDATE class_sessions SET status='noted', notes_json=? WHERE id=?", (json.dumps(notes), s))
    head = json.dumps(class_ask.notes_for_chat(ctx.con, "last"))[:6000]   # what a cut tool result still shows
    assert "Asked why a catalyst" in head and "Redo the catalyst question" in head
