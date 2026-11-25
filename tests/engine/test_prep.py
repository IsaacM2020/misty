"""Suggestion prep: plans are checked before anything acts, Go runs under Isaac's own say-so, drafts are never sent."""
import json

import pytest

from misty_engine import openrouter, prep, suggest
from misty_engine.errors import Fatal
from misty_engine.scheduler import Scheduler


class FakeBrain:
    """Stands in for ctx.openrouter: answers with a fixed model reply and records whether Go approved the call."""
    def __init__(self, reply: str | Exception):
        self.reply, self.calls, self.approved = reply, 0, []

    async def post(self, path, body, **kw):
        self.calls += 1
        self.approved.append(openrouter._USER_APPROVED.get())
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"choices": [{"message": {"content": self.reply}}], "usage": {"cost": 0.0003}}


def card(ctx, add_item, clock, kind="calendar", text="Add Shveta's Zoom on 31 Oct", locator=None, source="imessage"):
    item = add_item("it_" + kind + str(clock.now()), source=source, kind="chat_week",
                    locator=locator or "imessage:any;-;+16283094538:2026-W43", title="Forrest AI · 2026-W43")
    return suggest.add(ctx.con, item, kind, text, "this_week", None, clock)


EVENT = json.dumps({"plan": "calendar", "summary": "Add Zoom with Shveta, Sat 31 Oct 1:30pm",
                    "event": {"title": "Mentorship Zoom with Shveta", "start": "2026-10-31T13:30", "end": "2026-10-31T14:30"}})


def test_parse_keeps_good_plans_and_turns_bad_ones_into_none():
    assert prep.parse(EVENT)["event"]["start"] == "2026-10-31T13:30"
    assert prep.parse('{"plan": "calendar", "event": {"title": "x", "start": "next friday"}}')["plan"] == "none"
    assert prep.parse("sure! I'd love to help")["plan"] == "none"
    assert prep.parse('{"plan": "delete_everything"}')["plan"] == "none"
    assert prep.parse('{"plan": "draft", "draft": {"text": "yep, confirmed!"}}')["draft"]["text"] == "yep, confirmed!"


def test_chat_targets_come_from_the_item_locator():
    row = lambda loc: {"locator": loc}
    assert prep.chat_target(row("whatsapp:6591542915@s.whatsapp.net:2026-W42")) == ("whatsapp", "6591542915")
    assert prep.chat_target(row("whatsapp:1203630@g.us:2026-W42")) == ("group", "")
    assert prep.chat_target(row("imessage:any;-;+16283094538:2026-W43")) == ("imessage", "+16283094538")
    assert prep.chat_target(row("gmail:thread:1a152889088861d1")) == ("gmail", "1a152889088861d1")


@pytest.mark.asyncio
async def test_go_plans_under_isaacs_approval_and_adds_the_event(ctx, add_item, clock, monkeypatch):
    sid = card(ctx, add_item, clock)
    ctx.openrouter = FakeBrain(EVENT)
    ran = []

    async def osa(script, *args):
        ran.append(args)
        return "made"
    monkeypatch.setattr(prep, "_osa", osa)
    out = await prep.go(ctx, sid)
    assert out["state"] == "done" and out["note"].startswith("Added to Calendar: Mentorship Zoom with Shveta")
    assert ctx.openrouter.approved == [True]                      # the tap is the approval, even with paid calls locked
    assert ran[0][:2] == (prep.CALENDAR, "Mentorship Zoom with Shveta")
    assert ran[0][4:9] == ("2026", "10", "31", "13", "30")           # passed as arguments, never pasted into the script
    row = prep.card_row(ctx.con, sid)
    assert row["prep_state"] == "done" and row["status"] == "new"   # Isaac still ticks it off himself


@pytest.mark.asyncio
async def test_go_on_an_existing_event_says_so(ctx, add_item, clock, monkeypatch):
    sid = card(ctx, add_item, clock)
    ctx.openrouter = FakeBrain(EVENT)

    async def osa(script, *args):
        return "exists:Mentorship session"
    monkeypatch.setattr(prep, "_osa", osa)
    assert (await prep.go(ctx, sid))["note"].startswith("Already on your calendar")


@pytest.mark.asyncio
async def test_a_none_plan_does_nothing(ctx, add_item, clock, monkeypatch):
    sid = card(ctx, add_item, clock, kind="task", text="Free up 20G on hdd1")
    ctx.openrouter = FakeBrain('{"plan": "none", "why": "hdd1 is on the Raspberry Pi"}')

    async def osa(*a):
        raise AssertionError("must not act")
    monkeypatch.setattr(prep, "_osa", osa)
    out = await prep.go(ctx, sid)
    assert out == {"state": "none", "note": "hdd1 is on the Raspberry Pi"}


@pytest.mark.asyncio
async def test_a_whatsapp_draft_opens_the_chat_with_text_and_never_sends(ctx, add_item, clock, monkeypatch):
    sid = card(ctx, add_item, clock, kind="reply", text="Confirm to Dada", source="whatsapp",
               locator="whatsapp:6591542915@s.whatsapp.net:2026-W42")
    ctx.openrouter = FakeBrain('{"plan": "draft", "summary": "Draft to Dada", "draft": {"text": "Yes, 6 Dec works & set!"}}')
    opened = []

    async def op(url):
        opened.append(url)
    monkeypatch.setattr(prep, "_open", op)
    out = await prep.go(ctx, sid)
    assert opened == ["whatsapp://send?phone=6591542915&text=Yes%2C%206%20Dec%20works%20%26%20set%21"]
    assert "press send" in out["note"]


@pytest.mark.asyncio
async def test_background_prep_stops_quietly_when_paid_calls_are_locked(ctx, add_item, clock):
    sid = card(ctx, add_item, clock)
    ctx.openrouter = FakeBrain(Fatal("PAID_LOCKED"))
    await prep.prep_cards(None, ctx)
    assert prep.card_row(ctx.con, sid)["prep_state"] is None        # still waiting; Go will plan it on the tap


@pytest.mark.asyncio
async def test_stream_plan_fails_cleanly_without_hands(ctx, add_item, clock):
    sid = card(ctx, add_item, clock, kind="task", text="Open the TOK exhibition sheet")
    ctx.openrouter = FakeBrain('{"plan": "stream", "task": "In Safari open docs.google.com"}')
    out = await prep.go(ctx, sid)
    assert out == {"state": "failed", "note": "Couldn't: Stream is not connected"}


def test_scheduler_preps_only_while_paid_calls_are_allowed(fctx, add_item, clock):
    fctx.settings.set("phase", "live")
    card(fctx, add_item, clock)
    s = Scheduler(fctx)
    fctx.settings.set("paid_calls_allowed", False)
    s._prep_cards()
    n = lambda: fctx.con.execute("select count(*) from jobs where kind='prep_cards'").fetchone()[0]
    assert n() == 0
    fctx.settings.set("paid_calls_allowed", True)
    s._prep_cards()
    s._prep_cards()
    assert n() == 1
