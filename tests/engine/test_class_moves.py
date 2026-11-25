"""class_start and class_link_save: "start my class" by voice or chat opens the join link AND starts the recording (the app
does the recording, over the hands wire, exactly as the reach-out card's Yes does); a class link he tells her is kept and used.
The failure that started this: 19:01 "Can you start my English class, please?" got "I can't start the recording from here"."""
import time
from datetime import datetime, timedelta

import pytest

from misty_engine import chat, class_reach, classes, moves
from misty_engine.clock import SGT
from misty_engine.moves import registry

NOW = datetime(2026, 11, 21, 19, 1, tzinfo=SGT)          # a Saturday, 1 minute into the 19:00 English class
MEET = "https://meet.google.com/abc-defg-hij"
ZOOM = "https://us02web.zoom.us/j/84512345678"


def ev(title, start, mins=60, link=None, all_day=False):
    return {"start": start, "end": start + timedelta(minutes=mins), "all_day": all_day, "title": title, "calendar": "", "location": "",
            "link": link}


def at(h, m=0):
    return NOW.replace(hour=h, minute=m)


class AppHands:
    """The app's side of `class_start`: it records a live class the way ClassVM.start does, and answers like AppState does."""
    connected = True

    def __init__(self, ctx, status="started", problem=None):
        self.ctx, self.status, self.problem, self.calls = ctx, status, problem, []

    async def _call(self, op, **a):
        self.calls.append((op, a))
        if self.problem:
            return {"ok": False, "status": "failed", "problem": self.problem}
        if self.status == "started":
            classes.start(self.ctx.con, self.ctx.clock, a["subject"], classes.dated(a["title"], self.ctx.clock.now()), None,
                          "online" if a["online"] else "room")
        return {"ok": True, "status": self.status}


@pytest.fixture
def cls(ctx, clock, monkeypatch):
    """ctx at 19:01 with a calendar read a moment ago, the app connected, and `chat._open` recorded instead of opening a browser."""
    clock.t = NOW.timestamp()
    ctx.extras = {"hands": AppHands(ctx)}
    opened = []

    async def fake_open(target):
        opened.append(target)
    monkeypatch.setattr(chat, "_open", fake_open)

    def calendar(*events):
        ctx.extras["calendar_cache"] = {"window": (time.monotonic(), list(events), NOW.date().isoformat())}
    ctx.opened, ctx.calendar = opened, calendar
    return ctx


async def run(ctx, name, params=None, call="c1"):
    return await moves.run(ctx, name, params or {}, call, source="him")


@pytest.mark.asyncio
async def test_start_my_class_records_the_class_that_is_on_and_opens_its_link(cls):
    cls.calendar(ev("English Class", at(19), link=MEET), ev("Homework block", at(18), 180))
    r = await run(cls, "class_start")
    assert r["ok"] and r["verified"], r
    assert r["what"] == "Recording English Class. Opened its link."
    assert cls.opened == [MEET]
    op, a = cls.extras["hands"].calls[0]
    assert op == "class_start" and a["title"] == "English Class" and a["subject"] == "English" and a["online"] is True
    assert classes.list(cls.con)[0]["subject"] == "English"            # a live class exists: that is what "verified" read back


@pytest.mark.asyncio
async def test_a_class_starting_within_fifteen_minutes_counts_and_one_later_does_not(cls):
    cls.calendar(ev("Physics class", at(19, 14), link=ZOOM))
    r = await run(cls, "class_start")
    assert r["ok"] and r["what"].startswith("Recording Physics class.")
    cls.con.execute("UPDATE class_sessions SET status='ended'")
    cls.calendar(ev("Physics class", at(19, 40), link=ZOOM))
    late = await run(cls, "class_start", call="c2")
    assert late["ok"] is False and "no class on right now" in late["what"] and len(cls.extras["hands"].calls) == 1


@pytest.mark.asyncio
async def test_a_title_picks_that_class_not_the_other_one_on(cls):
    cls.calendar(ev("Prashant Physics Class", at(19), link=ZOOM), ev("English Class", at(19), link=MEET))
    r = await run(cls, "class_start", {"title": "my physics class"})
    assert r["ok"] and cls.opened == [ZOOM] and cls.extras["hands"].calls[0][1]["subject"] == "Physics"


@pytest.mark.asyncio
async def test_no_link_on_the_calendar_uses_the_saved_one(cls):
    cls.calendar(ev("English Class", at(19)))
    saved = await run(cls, "class_link_save", {"title": "English class", "url": MEET}, "s1")
    assert saved["ok"] and saved["verified"], saved
    r = await run(cls, "class_start")
    assert r["ok"] and cls.opened == [MEET] and r["what"] == "Recording English Class. Opened its link."
    assert cls.extras["hands"].calls[0][1]["online"] is True              # a saved link means it is held online


@pytest.mark.asyncio
async def test_the_calendar_link_wins_over_a_saved_one(cls):
    cls.calendar(ev("English Class", at(19), link=ZOOM))
    await run(cls, "class_link_save", {"title": "English", "url": MEET}, "s1")
    await run(cls, "class_start")
    assert cls.opened == [ZOOM]


@pytest.mark.asyncio
async def test_no_link_anywhere_records_and_says_so_in_a_few_words(cls):
    cls.calendar(ev("English Class", at(19)))
    r = await run(cls, "class_start")
    assert r["ok"] and cls.opened == [] and r["what"] == "Recording English Class. No join link on the calendar or saved for it."
    assert cls.extras["hands"].calls[0][1]["online"] is False


@pytest.mark.asyncio
async def test_already_recording_does_not_start_twice_but_still_opens_the_link(cls):
    """The auto path started it at 18:58; "start my class" then means "take me to the link"."""
    cls.calendar(ev("English Class", at(19), link=MEET))
    classes.start(cls.con, cls.clock, "English", "English Class, 21 Nov", None, "online")
    r = await run(cls, "class_start")
    assert r["ok"] and r["verified"] and r["what"] == "Already recording English Class. Opened its link."
    assert cls.extras["hands"].calls == [] and cls.opened == [MEET]


@pytest.mark.asyncio
async def test_the_reach_out_card_is_marked_handled(cls):
    cls.calendar(ev("English Class", at(19), link=MEET))
    await run(cls, "class_start")
    assert any(k.startswith("English Class@") for k in class_reach.handled(cls))


@pytest.mark.asyncio
async def test_a_voice_turn_queues_the_start_and_it_still_counts(cls):
    cls.calendar(ev("English Class", at(19), link=MEET))
    cls.extras["hands"].status = "queued"          # the app waits for her reply to be spoken before it takes the mic
    r = await run(cls, "class_start")
    assert r["ok"] and r["verified"] and r["what"].startswith("Recording English Class.")


@pytest.mark.asyncio
async def test_no_class_on_is_an_honest_short_reply(cls):
    cls.calendar(ev("Homework block", at(19), 120), ev("Maths", at(9)))
    r = await run(cls, "class_start")
    assert r["ok"] is False and r["what"] == "there's no class on right now or starting in the next 15 minutes"
    assert cls.opened == [] and cls.extras["hands"].calls == []


@pytest.mark.asyncio
async def test_no_calendar_and_no_title_says_it_cannot_see_it(cls):
    cls.extras.pop("calendar_cache", None)
    cls.net.online = False
    r = await run(cls, "class_start")
    assert r["ok"] is False and "can't see his calendar" in r["what"]


@pytest.mark.asyncio
async def test_a_named_class_the_calendar_lacks_is_still_started(cls):
    cls.calendar(ev("Maths", at(9)))
    r = await run(cls, "class_start", {"title": "English class"})
    assert r["ok"] and cls.extras["hands"].calls[0][1]["title"] == "English class"


@pytest.mark.asyncio
async def test_the_app_not_connected_never_claims_a_recording_but_still_opens_the_link(cls):
    cls.calendar(ev("English Class", at(19), link=MEET))
    cls.extras = {"calendar_cache": cls.extras["calendar_cache"]}
    r = await run(cls, "class_start")
    assert r["ok"] is False and "app isn't connected" in r["what"] and cls.opened == [MEET]
    assert "Recording" not in r["what"]


@pytest.mark.asyncio
async def test_the_app_failing_to_record_is_reported(cls):
    cls.calendar(ev("English Class", at(19), link=MEET))
    cls.extras["hands"].problem = "Mic is off: Settings > Permissions"
    r = await run(cls, "class_start")
    assert r["ok"] is False and "Mic is off" in r["what"] and cls.con.execute("SELECT COUNT(*) FROM class_sessions").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_it_never_runs_on_text_she_read(cls):
    cls.calendar(ev("English Class", at(19), link=MEET))
    r = await moves.run(cls, "class_start", {}, "t1", source="text")
    assert r["ok"] is False and cls.extras["hands"].calls == []


# ---- class_link_save

@pytest.mark.asyncio
async def test_a_class_link_is_kept_for_that_class_only(cls):
    assert (await run(cls, "class_link_save", {"title": "English class", "url": MEET}, "a"))["verified"]
    assert (await run(cls, "class_link_save", {"title": "Prashant physics class", "url": ZOOM}, "b"))["verified"]
    rows = [dict(r) for r in cls.con.execute("SELECT key, text, link, link_title FROM facts WHERE status='active'")]
    from misty_engine import calendar_now
    assert calendar_now.link_from_facts(rows, "English Class", "English") == MEET
    assert calendar_now.link_from_facts(rows, "Prashant Class", "Physics") == ZOOM
    again = await run(cls, "class_link_save", {"title": "english", "url": ZOOM + "9"}, "c")        # a newer link replaces the old one
    assert again["ok"] and calendar_now.link_from_facts(
        [dict(r) for r in cls.con.execute("SELECT key, text, link, link_title FROM facts WHERE status='active'")], "English Class", "English") == ZOOM + "9"


@pytest.mark.asyncio
async def test_class_link_save_refuses_what_is_not_a_meeting_link(cls):
    for bad in ("https://example.com/page", "http://zoom.us/j/1", "see https://zoom.us/j/123456789"):
        r = await run(cls, "class_link_save", {"title": "English class", "url": bad}, "x" + bad[-4:])
        assert r["ok"] is False
    assert cls.con.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    r = await run(cls, "class_link_save", {"title": "class", "url": MEET}, "y")
    assert r["ok"] is False and "which class" in r["what"]


def test_both_moves_are_registered_with_their_pick_lines():
    for name in ("class_start", "class_link_save"):
        m = registry.MOVES[name]
        assert m.risk == "safe" and len(m.says) >= 3 and len(m.not_mine) >= 2 and m.no_text
    assert "class_start(title?)" in registry.catalog() and "class_link_save(title, url)" in registry.catalog()


@pytest.mark.asyncio
async def test_class_now_uses_the_same_saved_link(cls):
    """api_class.class.now and class_start share one way to find a saved link."""
    await run(cls, "class_link_save", {"title": "English class", "url": MEET}, "a")
    cur = {"title": "English Class", "online": False, "link": None, "subject": "English"}
    assert class_reach.with_saved_link(cls, cur)["link"] == MEET and cur["online"] is True
