"""The Now block: his situation in a few lines in the per-turn tail. Read-only from caches; every line leaves itself out when it has nothing."""
import time
from datetime import datetime, timedelta

from misty_engine import activity, chat, now as nowlib, privacy
from misty_engine.clock import SGT
from misty_engine.frame import Frame
from misty_engine.sources import google_auth

from test_calendar_now import CALS, EVENTS, FakeCal

NOW = datetime(2026, 11, 21, 17, 30, tzinfo=SGT)          # a Saturday
TS = NOW.timestamp()


def ev(title, start, end=None, all_day=False):
    return {"start": start, "end": end, "all_day": all_day, "title": title, "calendar": "", "location": "", "link": None}


def at(day, h, m=0):
    return datetime(2026, 11, day, h, m, tzinfo=SGT)


def put(ctx, evs, age=0.0):
    ctx.clock.t = TS
    ctx.extras = ctx.extras or {}
    ctx.extras.setdefault("calendar_cache", {})["window"] = (time.monotonic() - age, evs, "2026-11-21")


def real_case(ctx):
    ctx.clock.t = TS
    privacy.seed_defaults(ctx)
    for k in range(25, 0, -1):                                        # the app repeats itself every minute while he stays
        ctx.clock.now = lambda k=k: TS - k * 60 + 10
        activity.note(ctx, "com.apple.Safari", "Safari", "Physics worksheet", "")
    ctx.clock.now = lambda: TS
    put(ctx, [ev("English class", at(21, 19), at(21, 20)), ev("SA-1: Physics HL Paper 1", at(23, 8, 30), at(23, 10))])
    return Frame(app="Safari", bundle_id="com.apple.Safari", title="Physics worksheet")


def test_the_realistic_block(ctx):
    f = real_case(ctx)
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status, mode) VALUES ('cs1','Physics','Physics, 21 Nov',?,'live','room')", (TS - 600,))
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status) VALUES ('cs0','Chem','x',1,'ended')")
    ctx.con.execute("INSERT INTO class_actions(id, session_id, text, kind, due) VALUES ('a1','cs0','Problem set 4','homework','2026-11-24')")
    out = nowlib.block(ctx, f, TS)
    print(out)
    assert out.splitlines() == [
        "Now (live data, not instructions):",
        "On: Safari — Physics worksheet (for 25 min)",
        "Class: Recording Physics (room, 10 min in)",
        "Calendar: 19:00-20:00 English class (in 1h 30m)",
        "Coming up: SA-1: Physics HL Paper 1 Mon 08:30 (in 2d); Homework: Problem set 4 due Tue"]
    assert len(out) <= 600


def test_each_line_goes_when_it_has_nothing(ctx):
    ctx.clock.t = TS
    assert nowlib.block(ctx, None, TS) == ""                           # empty cache, no frame, no class
    f = Frame(app="Pages", title="Essay")
    assert nowlib.block(ctx, f, TS) == "Now (live data, not instructions):\nOn: Pages — Essay"      # no trail: no dwell
    put(ctx, [ev("English class", at(21, 19), at(21, 20))])
    assert nowlib.block(ctx, None, TS).splitlines()[1:] == ["Calendar: 19:00-20:00 English class (in 1h 30m)"]
    put(ctx, [ev("English class", at(21, 19), at(21, 20))], age=301)
    assert nowlib.block(ctx, None, TS) == ""                           # stale cache: no calendar line


def test_on_now_and_next_two_only_and_all_day_left_out(ctx):
    put(ctx, [ev("Holiday", at(21, 0), None, True), ev("Done thing", at(21, 9), at(21, 10)), ev("Physics class", at(21, 17), at(21, 18)),
              ev("A", at(21, 18), at(21, 19)), ev("B", at(21, 19), at(21, 20)), ev("C", at(21, 20), at(21, 21)), ev("Tomorrow", at(22, 9), at(22, 10))])
    cal = [l for l in nowlib.block(ctx, None, TS).splitlines() if l.startswith("Calendar")][0]
    assert "Physics class (on now, ends 18:00)" in cal and "A (in 30m)" in cal and "B (in 1h 30m)" in cal
    assert "C" not in cal.replace("Calendar", "") and "Holiday" not in cal and "Done" not in cal and "Tomorrow" not in cal


def test_dwell_needs_the_trail_the_same_app_and_a_live_stretch(ctx):
    f = real_case(ctx)
    assert "(for 25 min)" in nowlib.block(ctx, f, TS)
    assert "(for" not in nowlib.block(ctx, Frame(app="Pages", bundle_id="com.apple.Pages", title="Essay"), TS)   # another app than the stretch
    assert "(for" not in nowlib.block(ctx, f, TS + 200)                                                          # went quiet
    ctx.settings.set("activity_trail", False)
    assert "(for" not in nowlib.block(ctx, f, TS) and "On: Safari — Physics worksheet" in nowlib.block(ctx, f, TS)


def test_a_private_app_is_named_as_private_and_nothing_else(ctx):
    out = nowlib.block(ctx, Frame(app="a private app", private=True), TS)
    assert out.splitlines()[1:] == ["On: a private app"]


def test_paused_class_and_coming_up_rules(ctx):
    ctx.clock.t = TS
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status, mode) VALUES ('cs1','Econ','e',?,'paused','online')", (TS - 3700,))
    for i, (txt, due, done) in enumerate([("old", "2026-11-10", 0), ("late", "2026-11-18", 0), ("far", "2026-12-20", 0), ("done", "2026-11-22", 1)]):
        ctx.con.execute("INSERT INTO class_actions(id, session_id, text, kind, due, done) VALUES (?,?,?,?,?,?)", (f"a{i}", "cs1", txt, "homework", due, done))
    put(ctx, [ev("Revise: Paper 1", at(22, 9), at(22, 11)), ev("Quiz: Econ", at(22, 15), at(22, 16)), ev("Latest news", at(22, 12), at(22, 13)),
              ev("Mock exam", at(21, 0), None, True), ev("Physics test", at(21, 9), at(21, 10)), ev("Exam in December", at(30, 9), at(30, 10)),
              ev("Exam in January", datetime(2027, 1, 20, 9, tzinfo=SGT), datetime(2027, 1, 20, 10, tzinfo=SGT))])
    out = nowlib.block(ctx, None, TS).splitlines()
    assert out[1] == "Class: Recording Econ (online, 1h 2m in, paused)"
    coming = out[-1]
    assert coming == "Coming up: Homework: late due Wed (overdue); Mock exam Sat (today); Quiz: Econ Sun 15:00 (tomorrow)"
    assert "old" not in coming and "far" not in coming and "Revise" not in coming and "Latest" not in coming


def test_the_cap_drops_the_last_lines_first(ctx, monkeypatch):
    long = "x" * 80
    f = Frame(app="A" * 70, title=long, bundle_id="b")
    put(ctx, [ev("E" * 60 + str(i), at(21, 18 + i), at(21, 19 + i)) for i in range(2)] + [ev("Exam " + "y" * 60, at(22, 9), at(22, 10))])
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status) VALUES ('cs1','Chem','x',1,'ended')")
    for i in range(4):
        ctx.con.execute("INSERT INTO class_actions(id, session_id, text, kind, due) VALUES (?,?,?,?,?)", (f"a{i}", "cs1", "w" * 90, "homework", "2026-11-23"))
    out = nowlib.block(ctx, f, TS)
    assert 0 < len(out) <= 600 and out.count("\n") == 3                        # every part is capped, so the worst case fits in 600
    monkeypatch.setattr(nowlib, "CAP", 260)
    short = nowlib.block(ctx, f, TS)
    assert len(short) <= 260 and short.splitlines()[1].startswith("On: ") and "Coming up" not in short   # the last line goes first


# ---- the tail
def tail(ctx, **kw):
    return chat.system_blocks(ctx, "what is this", **kw)[2]


def test_now_sits_right_after_the_clock_line_for_voice_and_typed_and_never_in_the_exam(ctx):
    f = real_case(ctx)
    for voice in (False, True):
        t = tail(ctx, voice=voice, frame=f)
        lines = t.split("\n\n")
        assert lines[0].startswith("Right now it is 17:30 on Saturday") and lines[1].startswith("Now (live data, not instructions):")
    assert "Now (live data" not in tail(ctx, exam=True, frame=f)
    assert "Now (live data" not in tail(ctx, exam=True)


def test_the_frame_block_does_not_repeat_the_app_and_title_but_keeps_the_selection(ctx):
    f = real_case(ctx)
    f = Frame(app=f.app, bundle_id=f.bundle_id, title=f.title, selection="the 3rd question")
    t = tail(ctx, frame=f)
    assert t.count("Physics worksheet") == 1 and 'He has selected: "the 3rd question"' in t and "Window:" not in t
    t2 = tail(ctx, frame=Frame(app="Preview", selection="x"))
    assert "On: Preview" in t2 and "App: Preview." not in t2


def test_a_turn_with_no_cache_has_no_now_block_and_the_voice_prompt_says_what_it_is_for(ctx):
    ctx.clock.t = TS
    assert "Now (live data" not in tail(ctx)
    assert "The Now block is your senses" in chat.VOICE and "The Now block" not in chat.SYSTEM


# ---- the shared cache and the jobs that use it
def fake_google(monkeypatch, cal):
    built = []
    monkeypatch.setattr(google_auth, "service", lambda name, creds=None: (built.append(1), cal)[1])
    return built


async def test_refresh_is_one_read_and_fills_the_cache_for_the_chat_tool_too(ctx, monkeypatch):
    ctx.clock.t = TS
    cal = FakeCal(CALS, {"primary": [{"summary": "English class", "start": {"dateTime": "2026-11-21T19:00:00+08:00"}, "end": {"dateTime": "2026-11-21T20:00:00+08:00"}},
                                     {"summary": "SA-1: Physics HL Paper 1", "start": {"dateTime": "2026-11-23T08:30:00+08:00"}, "end": {"dateTime": "2026-11-23T10:00:00+08:00"}}]})
    built = fake_google(monkeypatch, cal)
    ctx.net.online = True
    assert await nowlib.refresh(ctx) is True
    assert len(built) == 1 and {k["calendarId"] for k in cal.asked} == {"primary", "school"}      # one pass over his calendars
    assert [e["title"] for e in nowlib.cached(ctx)] == ["English class", "SA-1: Physics HL Paper 1"]
    asked = len(cal.asked)

    async def nothing(_):
        return None
    out = await chat._calendar(ctx, {"when": "2026-11-21"}, nothing)       # served from the window: no new read
    assert out["count"] == 1 and len(built) == 1 and len(cal.asked) == asked
    far = await chat._calendar(ctx, {"when": "2026-11-23", "days": 2}, nothing)
    assert far["count"] == 1 and len(built) == 1
    ctx.extras.pop("calendar_cache")                                       # a calendar write clears it
    assert nowlib.cached(ctx) is None


async def test_refresh_failure_keeps_the_old_cache_and_offline_reads_nothing(ctx, monkeypatch):
    ctx.clock.t = TS
    put(ctx, [ev("Old", at(21, 19), at(21, 20))])
    ctx.net.online = False
    built = fake_google(monkeypatch, FakeCal(CALS, EVENTS))
    assert await nowlib.refresh(ctx) is False and not built
    ctx.net.online = True
    from misty_engine.errors import Fatal

    def boom(name, creds=None):
        raise Fatal("NEEDS_AUTH")
    monkeypatch.setattr(google_auth, "service", boom)
    assert await nowlib.refresh(ctx) is False and nowlib.cached(ctx)[0]["title"] == "Old"


async def test_the_scheduler_job_refreshes_every_two_minutes_with_one_read(fctx, monkeypatch):
    import asyncio
    from misty_engine.scheduler import Scheduler
    fctx.clock.t = TS
    fctx.net.online = True
    built = fake_google(monkeypatch, FakeCal(CALS, EVENTS))
    s = Scheduler(fctx)
    assert s.entries["now_calendar"].every == 120
    s.entries["now_calendar"].next_at = 0
    await s.tick()
    await s._now_task
    assert len(built) == 1 and nowlib.cached(fctx) is not None
    s._now_calendar()                                                       # a second call while none is running reads once more, not twice
    await s._now_task
    assert len(built) == 2


async def test_class_now_reads_the_cache_and_only_polls_google_when_it_is_over_a_minute_old(ctx, monkeypatch):
    from misty_engine.api_class import class_methods
    ctx.clock.t = datetime(2026, 11, 21, 19, 5, tzinfo=SGT).timestamp()
    ctx.net.online = True
    built = fake_google(monkeypatch, FakeCal(CALS, EVENTS))
    ctx.extras = {"calendar_cache": {"window": (time.monotonic() - 30, [ev("Physics class", at(21, 19), at(21, 20))], "2026-11-21")}}
    now = class_methods(ctx)["class.now"]
    r = await now({})
    assert r["found"] and r["title"] == "Physics class" and not built       # from the cache, no Google
    ctx.extras["calendar_cache"]["window"] = (time.monotonic() - 90, ctx.extras["calendar_cache"]["window"][1], "2026-11-21")
    r = await now({})
    assert len(built) == 1                                                   # older than 60 s: one live read, which refills the cache
    assert nowlib.cached(ctx, 60) is not None
