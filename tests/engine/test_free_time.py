"""Free-slot finder, the calendar tool's free flag, and calendar_add_many: one yes for a whole revision plan."""
import json
from datetime import date, datetime

import pytest

from misty_engine import calendar_now, chat, moves
from misty_engine.clock import SGT
from misty_engine.moves import calendar as calmove
from misty_engine.sources import google_auth

from fakes_google import FakeCalWrite
from test_calendar_now import FakeCal, ev
from test_chat import ScriptBrain, call, say


def e(title, a, b, all_day=False):
    t = lambda x: datetime.fromisoformat(x).replace(tzinfo=SGT)
    return {"start": t(a), "end": None if all_day else t(b), "all_day": all_day, "title": title}


NOW = datetime(2026, 11, 21, 8, 0, tzinfo=SGT)
D = date(2026, 11, 21)


def slots(events, days=1, now=NOW):
    return [f"{a:%H:%M}-{b:%H:%M}" for a, b in calendar_now.free_slots(events, D, days, now)]


def test_empty_day_is_the_whole_window():
    assert slots([]) == ["09:00-22:00"]


def test_buffer_around_events_and_overlaps_merge():
    evs = [e("a", "2026-11-21T10:00", "2026-11-21T11:00"), e("b", "2026-11-21T10:30", "2026-11-21T12:00")]
    assert slots(evs) == ["09:00-09:45", "12:15-22:00"]


def test_short_gaps_are_dropped_and_exactly_45_is_kept():
    evs = [e("a", "2026-11-21T09:00", "2026-11-21T10:00"), e("b", "2026-11-21T11:00", "2026-11-21T11:30"),
           e("c", "2026-11-21T12:30", "2026-11-21T22:30")]
    # gap 10:15-10:45 is 30 min (dropped); 11:45-12:15 is 30 min (dropped); nothing after 12:45
    assert slots(evs) == []
    evs = [e("a", "2026-11-21T09:00", "2026-11-21T10:00"), e("b", "2026-11-21T11:30", "2026-11-21T22:30")]
    assert slots(evs) == ["10:15-11:15"]               # exactly 60 minus buffers
    assert slots([e("a", "2026-11-21T09:00", "2026-11-21T10:00"), e("b", "2026-11-21T11:15", "2026-11-21T22:30")]) == ["10:15-11:00"]
    assert slots([e("a", "2026-11-21T09:00", "2026-11-21T10:00"), e("b", "2026-11-21T11:14", "2026-11-21T22:30")]) == []


def test_all_day_events_block_nothing_and_an_event_without_an_end_still_blocks():
    assert slots([e("Holiday", "2026-11-21T00:00", "", all_day=True)]) == ["09:00-22:00"]
    open_ended = {"start": datetime(2026, 11, 21, 12, 0, tzinfo=SGT), "end": None, "all_day": False, "title": "x"}
    assert slots([open_ended]) == ["09:00-11:45", "12:15-22:00"]


def test_nothing_in_the_past_and_now_rounds_up_to_the_quarter_hour():
    now = datetime(2026, 11, 21, 13, 7, tzinfo=SGT)
    assert slots([], now=now) == ["13:15-22:00"]
    assert slots([], now=datetime(2026, 11, 21, 21, 30, tzinfo=SGT)) == []          # 30 minutes left is too short
    assert slots([], days=2, now=datetime(2026, 11, 21, 23, 0, tzinfo=SGT)) == ["09:00-22:00"]   # tomorrow only
    assert slots([], now=datetime(2026, 11, 21, 13, 0, tzinfo=SGT)) == ["13:00-22:00"]


def test_an_event_that_runs_past_midnight_blocks_the_next_morning_and_days_are_independent():
    evs = [e("late", "2026-11-21T21:00", "2026-11-22T10:00")]
    assert slots(evs, days=2) == ["09:00-20:45", "10:15-22:00"]


def test_lines_are_iso_dated_so_they_can_go_into_the_batch_add():
    out = calendar_now.free_lines(calendar_now.free_slots([e("a", "2026-11-21T10:00", "2026-11-21T11:00")], D, 1, NOW))
    assert out == ["2026-11-21 09:00-09:45 (Sat, 45m)", "2026-11-21 11:15-22:00 (Sat, 10h 45m)"]


CALS = [{"id": "primary", "summary": "Isaac", "accessRole": "owner"}]


@pytest.mark.asyncio
async def test_calendar_tool_returns_free_slots_only_when_asked(ctx, monkeypatch):
    ctx.clock.t = datetime(2026, 11, 21, 8, 0, tzinfo=SGT).timestamp()
    evs = {"primary": [ev("Physics HL P1", "2026-11-23T08:30:00+08:00", "2026-11-23T10:30:00+08:00")]}
    monkeypatch.setattr(google_auth, "service", lambda name, creds=None: FakeCal(CALS, evs))
    ctx.net.online = True
    ctx.openrouter = ScriptBrain({"calls": [call("calendar", when="2026-11-23", days=1, free=True)]}, {"content": "ok"})
    await say(ctx, "plan my revision")
    tool = json.loads(ctx.openrouter.bodies[1]["messages"][-1]["content"])
    assert tool["free"] == ["2026-11-23 10:45-22:00 (Mon, 11h 15m)"] and "calendar_add_many" in tool["note"]
    ctx.openrouter = ScriptBrain({"calls": [call("calendar", when="2026-11-23")]}, {"content": "ok"})
    ctx.extras.pop("calendar_cache", None)
    await say(ctx, "what's on monday")
    assert "free" not in json.loads(ctx.openrouter.bodies[1]["messages"][-1]["content"])


def test_the_tool_and_the_voice_prompt_know_the_flow():
    cal = next(t for t in chat.TOOLS if t["function"]["name"] == "calendar")["function"]
    assert "free" in cal["parameters"]["properties"] and "free:true" in cal["description"]
    assert "calendar_add_many" in chat.VOICE and "calendar_add_many" in moves.catalog()


# ---- the batch add

@pytest.fixture
def cal(ctx, monkeypatch):
    ctx.clock.t = datetime(2026, 11, 21, 12, 0, tzinfo=SGT).timestamp()
    fake = FakeCalWrite()
    monkeypatch.setattr(calmove, "_svc", lambda: fake)
    monkeypatch.setattr(google_auth, "granted", lambda: set(google_auth.SCOPES))
    return fake


def asker(answer=True):
    asked = []

    async def c(q):
        asked.append(q)
        return answer
    return c, asked


PLAN = {"events": [
    {"title": "Revise: Physics HL P1", "start": "2026-11-21 14:00", "end": "2026-11-21 16:00"},
    {"title": "Revise: Physics HL P2", "start": "2026-11-22 09:00", "end": "2026-11-22 11:00"},
    {"title": "Revise: English P1", "start": "2026-11-22 14:00", "end": "2026-11-22 16:00"},
    {"title": "Revise: Chemistry HL", "start": "2026-11-23T16:00:00+08:00", "end": "2026-11-23T18:00:00+08:00"}]}


@pytest.mark.asyncio
async def test_batch_asks_once_with_one_short_line_adds_all_marked_and_undoes_in_one_go(ctx, cal):
    c, asked = asker()
    r = await moves.run(ctx, "calendar_add_many", PLAN, "b1", c)
    assert asked == ["Add 4 revision blocks, Saturday to Monday?"]
    assert r["ok"] and r["verified"] and len(r["event_ids"]) == 4 and len(cal.inserts) == 4
    assert all(i["extendedProperties"]["private"]["misty"] == "1" for i in cal.inserts)
    assert cal.inserts[3]["start"]["dateTime"].startswith("2026-11-23T16:00:00+08:00")
    u = await chat.run_tool(ctx, "move", {"action": "undo"}, on_status=lambda s: None, confirm=c)
    assert u["ok"] and sorted(cal.deletes) == sorted(r["event_ids"]) and asked[-1].startswith("Take this back?")


@pytest.mark.asyncio
async def test_batch_denied_adds_nothing(ctx, cal):
    c, asked = asker(False)
    r = await moves.run(ctx, "calendar_add_many", PLAN, "b2", c)
    assert r["declined"] and not cal.inserts and len(asked) == 1 and "didn't add" in r["say"]


@pytest.mark.asyncio
async def test_a_retry_never_doubles_and_two_same_title_blocks_one_day_both_go_in(ctx, cal):
    c, _ = asker()
    await moves.run(ctx, "calendar_add_many", PLAN, "b3", c)
    again = await moves.run(ctx, "calendar_add_many", PLAN, "b4", c)
    assert again["ok"] and again["event_ids"] == [] and again["skipped"] == 4 and len(cal.inserts) == 4
    twin = {"events": [{"title": "Revise: Chem", "start": "2026-11-24 09:00", "end": "2026-11-24 10:00"},
                       {"title": "Revise: Chem", "start": "2026-11-24 14:00", "end": "2026-11-24 15:00"}]}
    assert len((await moves.run(ctx, "calendar_add_many", twin, "b5", c))["event_ids"]) == 2


@pytest.mark.asyncio
async def test_bad_blocks_are_refused_before_he_is_asked(ctx, cal):
    c, asked = asker()
    bad = lambda **k: {"events": [{"title": "Revise: X", "start": "2026-11-22 09:00", "end": "2026-11-22 10:00", **k}]}
    for p, word in ((bad(start="2026-11-21 09:00", end="2026-11-21 10:00"), "past"), (bad(end="2026-11-22 08:00"), "end after"),
                    (bad(start="soon"), "2026-11-21 09:00"), (bad(title=""), "title"), (bad(end="2026-11-22 23:00"), "12 hours")):
        r = await moves.run(ctx, "calendar_add_many", p, "x" + word, c)
        assert not r["ok"] and word in r["error"]
    assert not asked and not cal.inserts
    assert "non-empty list" in (await moves.run(ctx, "calendar_add_many", {"events": []}, "x2", c))["error"]
    many = {"events": [{"title": "t", "start": "2026-11-22 09:00", "end": "2026-11-22 10:00"}] * 13}
    assert "too many" in (await moves.run(ctx, "calendar_add_many", many, "x3", c))["error"]


@pytest.mark.asyncio
async def test_a_failure_halfway_takes_back_what_went_in(ctx, cal):
    c, _ = asker()
    orig = cal.events().insert
    n = {"i": 0}

    class Boom(type(cal.events())):
        def insert(self, calendarId, body):
            n["i"] += 1
            if n["i"] == 3:
                raise ConnectionError("dropped")
            return orig(calendarId, body)
    cal.events = lambda: Boom(cal)
    r = await moves.run(ctx, "calendar_add_many", PLAN, "b6", c)
    assert not r["ok"] and "failed" in r["error"] and len(cal.deletes) == 2


@pytest.mark.asyncio
async def test_without_the_add_sign_in_nothing_is_written_and_remove_stays_all_or_nothing(ctx, cal, monkeypatch):
    c, _ = asker()
    mine = cal.add("primary", {"summary": "Dentist", "start": {"dateTime": "2026-11-24T10:00:00+08:00"}, "end": {"dateTime": "2026-11-24T11:00:00+08:00"}})
    r = await moves.run(ctx, "calendar_add_many", PLAN, "b7", c)
    ok = r["event_ids"][0]
    both = await moves.run(ctx, "calendar_remove", {"event_ids": f"{ok},{mine['id']}"}, "r1")
    assert not both["ok"] and not cal.deletes
    monkeypatch.setattr(google_auth, "granted", lambda: set(google_auth.READ_SCOPES))
    assert "sign in to Google once more" in (await moves.run(ctx, "calendar_add_many", PLAN, "b8", c))["what"]
