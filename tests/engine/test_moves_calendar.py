"""D4: calendar_add asks, adds one event, proves it, never adds two, and undo removes only events she made."""
import pytest

from misty_engine import chat, moves
from misty_engine.moves import calendar as calmove
from misty_engine.sources import google_auth

from fakes_google import FakeCalWrite


@pytest.fixture
def cal(ctx, monkeypatch):
    ctx.clock.t = 1_794_801_600.0                          # Mon 16 Nov 2026, 12:00 SGT
    fake = FakeCalWrite()
    monkeypatch.setattr(calmove, "_svc", lambda: fake)
    monkeypatch.setattr(google_auth, "granted", lambda: set(google_auth.SCOPES))
    return fake


async def st(_s): ...


def asker(answer=True):
    asked = []

    async def c(q):
        asked.append(q)
        return answer
    return c, asked


CHEM = {"title": "Chem test", "date": "friday", "time": "9"}


def test_dates_and_times():
    from datetime import date
    mon = date(2026, 11, 16)
    assert calmove.parse_date("friday", mon) == date(2026, 11, 20) and calmove.parse_date("tomorrow", mon) == date(2026, 11, 17)
    assert calmove.parse_date("2026-12-01", mon) == date(2026, 12, 1) and calmove.parse_date("someday", mon) is None
    assert [calmove.parse_time(t) for t in ("9", "9:30", "2pm", "2:30 pm", "12am", "14:00", "25:00", "x")] == \
        [(9, 0), (9, 30), (14, 0), (14, 30), (0, 0), (14, 0), None, None]


@pytest.mark.asyncio
async def test_add_asks_with_the_real_day_creates_one_marked_event_and_proves_it(ctx, cal):
    c, asked = asker()
    r = await moves.run(ctx, "calendar_add", CHEM, "c1", c)
    assert r["ok"] and r["verified"] and r["undo_available"]
    assert asked == ['Add "Chem test" on Fri 20 Nov, 9:00 to 10:00, to your main calendar?']
    ev = cal.inserts[0]
    assert ev["extendedProperties"]["private"]["misty"] == "1" and ev["start"]["dateTime"].startswith("2026-11-20T09:00:00+08:00")


@pytest.mark.asyncio
async def test_no_means_nothing_added(ctx, cal):
    c, _ = asker(False)
    r = await moves.run(ctx, "calendar_add", CHEM, "c1", c)
    assert r["declined"] and not cal.inserts and "didn't add" in r["say"]


@pytest.mark.asyncio
async def test_same_title_same_day_adds_nothing_even_with_a_new_call_id(ctx, cal):
    c, _ = asker()
    await moves.run(ctx, "calendar_add", CHEM, "c1", c)
    again = await moves.run(ctx, "calendar_add", {**CHEM, "time": "10am"}, "c2", c)
    assert again["ok"] and again["verified"] and again["already"] and len(cal.inserts) == 1 and "undo_available" not in again


@pytest.mark.asyncio
async def test_network_drop_after_save_then_a_retry_never_makes_two(ctx, cal):
    """D10 fault 2, now with the real move."""
    c, _ = asker()
    cal.drop_after_insert = True
    r = await moves.run(ctx, "calendar_add", CHEM, "d1", c)
    assert not r["ok"] and "failed" in r["error"]
    cal.drop_after_insert = False
    again = await moves.run(ctx, "calendar_add", CHEM, "d2", c)          # the model tries again with a new call
    assert again["already"] and len(cal.inserts) == 1


@pytest.mark.asyncio
async def test_without_the_new_sign_in_it_says_what_to_do_and_reading_is_untouched(ctx, cal, monkeypatch):
    monkeypatch.setattr(google_auth, "granted", lambda: set(google_auth.READ_SCOPES))
    c, _ = asker()
    r = await moves.run(ctx, "calendar_add", CHEM, "s1", c)
    assert not r["ok"] and "sign in to Google once more" in r["what"] and not cal.inserts


@pytest.mark.asyncio
async def test_verify_fails_when_the_read_back_does_not_find_it(ctx, cal):
    c, _ = asker()
    cal.lie = True
    r = await moves.run(ctx, "calendar_add", CHEM, "v1", c)
    assert r["ok"] and not r["verified"] and "do not say it worked" in r["note"]


@pytest.mark.asyncio
async def test_named_calendar_all_day_and_bad_input(ctx, cal):
    c, asked = asker()
    r = await moves.run(ctx, "calendar_add", {"title": "Sports day", "date": "2026-11-25", "calendar": "school"}, "n1", c)
    assert r["verified"] and "all day" in asked[0] and "school1" in cal.events_by_cal and cal.events_by_cal["school1"]
    assert not (await moves.run(ctx, "calendar_add", {"title": "x", "date": "2026-11-25", "calendar": "Holidays in Singapore"}, "n2", c))["ok"]
    assert "isn't clear" in (await moves.run(ctx, "calendar_add", {"title": "x", "date": "whenever"}, "n3", c))["what"]


@pytest.mark.asyncio
async def test_undo_deletes_her_event_after_asking(ctx, cal):
    c, asked = asker()
    await moves.run(ctx, "calendar_add", CHEM, "u1", c)
    r = await chat.run_tool(ctx, "move", {"action": "undo"}, on_status=st, confirm=c)
    assert r["ok"] and r["verified"] and r["undid"] == "calendar_add" and cal.deletes == ["e1"]
    assert asked[-1].startswith("Take this back?")


@pytest.mark.asyncio
async def test_remove_refuses_an_event_she_did_not_make(ctx, cal):
    mine = cal.add("primary", {"summary": "Dentist", "start": {"dateTime": "2026-11-18T10:00:00+08:00"}, "end": {"dateTime": "2026-11-18T11:00:00+08:00"}})
    r = await moves.run(ctx, "calendar_remove", {"event_id": mine["id"]}, "r1")
    assert not r["ok"] and "isn't one she added" in r["what"] and not cal.deletes
    assert "never runs" in (await moves.run(ctx, "calendar_remove", {"event_id": mine["id"]}, "r2", source="text"))["error"]
