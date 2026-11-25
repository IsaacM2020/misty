"""Edith reaches out first about class: auto / ask / none before a class, never twice for one event, quiet hours, and the
one-line "From Physics: ..." after the notes are done."""
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from misty_engine import class_reach, classes, gate
from misty_engine.clock import SGT

NOW = datetime(2026, 11, 21, 13, 29, tzinfo=SGT).timestamp()      # a Saturday, 1 min before a 13:30 class
KEY = "Prashant Class@2026-11-21T13:30:00+08:00"


@pytest.fixture
def ctx(con, clock, settings):
    clock.t = NOW
    c = SimpleNamespace(con=con, clock=clock, settings=settings, wiki=None, extras=None, sent=[])

    async def notify(method, params):
        c.sent.append((method, params))
    c.notify = notify
    gate.set_presence(c, 90.0, "idle")
    return c


def cur(starts_in=60.0, is_class=True, key=KEY, title="Prashant Class", subject=None):
    return {"title": title, "online": True, "link": "https://meet.google.com/x", "subject": subject,
            "cal_event_id": None, "starts_in": starts_in, "key": key, "is_class": is_class}


def recorded(ctx, title="Prashant Class", subject="Physics", day=14):
    s = classes.start(ctx.con, ctx.clock, subject, classes.dated(title, NOW - day * 86400), None)["id"]
    classes.end(ctx.con, ctx.clock, s)
    return s


def test_never_recorded_asks(ctx):
    assert class_reach.suggest(ctx, cur()) == {"suggest": "ask"}


def test_recorded_before_starts_by_itself_with_its_subject(ctx):
    recorded(ctx)
    assert class_reach.suggest(ctx, cur()) == {"suggest": "auto", "subject": "Physics"}
    assert class_reach.suggest(ctx, cur(subject="Math"))["suggest"] == "auto"      # a guessed subject is kept
    assert "subject" not in class_reach.suggest(ctx, cur(subject="Math"))


def test_another_title_is_not_the_same_class(ctx):
    recorded(ctx, "Chem tutor")
    assert class_reach.suggest(ctx, cur())["suggest"] == "ask"


def test_same_calendar_series_id_counts(ctx):
    s = classes.start(ctx.con, ctx.clock, "Physics", "Whatever he renamed it", "evt_series", "online")["id"]
    classes.end(ctx.con, ctx.clock, s)
    assert class_reach.earlier_session(ctx.con, "Prashant Class", "evt_series")["id"] == s


@pytest.mark.parametrize("starts_in,want", [(121, "none"), (120, "ask"), (-1, "ask"), (-599, "ask"), (-600, "none")])
def test_window_is_two_minutes_before_to_ten_after(ctx, starts_in, want):
    assert class_reach.suggest(ctx, cur(starts_in))["suggest"] == want


def test_none_for_a_non_class_a_live_class_and_no_event(ctx):
    assert class_reach.suggest(ctx, cur(is_class=False))["suggest"] == "none"
    assert class_reach.suggest(ctx, None)["suggest"] == "none"
    classes.start(ctx.con, ctx.clock, "Chem", None, None)
    assert class_reach.suggest(ctx, cur())["suggest"] == "none"


def test_quiet_hours_stay_quiet(ctx):
    ctx.clock.t = datetime(2026, 11, 21, 23, 30, tzinfo=SGT).timestamp()
    assert class_reach.suggest(ctx, cur())["suggest"] == "none"
    ctx.clock.t = datetime(2026, 11, 21, 6, 59, tzinfo=SGT).timestamp()
    assert class_reach.suggest(ctx, cur())["suggest"] == "none"


@pytest.mark.asyncio
async def test_once_per_event_then_none(ctx):
    assert await class_reach.reach(ctx, KEY, "Prashant Class", "ask") == "say"
    assert class_reach.suggest(ctx, cur())["suggest"] == "none"                   # handled: ignoring it = dismissed
    assert await class_reach.reach(ctx, KEY, "Prashant Class", "ask") == "skipped"
    assert len(ctx.sent) == 1
    other = "Prashant Class@2026-11-28T13:30:00+08:00"
    assert class_reach.suggest(ctx, cur(key=other))["suggest"] == "ask"           # next week's event is a new one


@pytest.mark.asyncio
async def test_class_start_lines_are_cards_never_spoken(ctx):
    for i, how in enumerate(("auto", "ask")):
        gate.set_presence(ctx, 90.0, "idle")
        await class_reach.reach(ctx, f"q{i}", "Prashant Class", how)
        ctx.clock.advance(3600)
    assert [m[1]["how"] for m in ctx.sent] == ["card", "card"]


@pytest.mark.asyncio
async def test_the_lines_and_a_busy_gate_drops_instead_of_holding(ctx):
    await class_reach.reach(ctx, KEY, "Prashant Class", "auto")
    assert ctx.sent[0][1]["text"] == "Recording Prashant Class."
    k2 = "Prashant Class@2026-11-22T13:30:00+08:00"
    ctx.clock.advance(3600)
    gate.set_presence(ctx, 90.0, "idle")
    await class_reach.reach(ctx, k2, "Prashant Class", "ask")
    assert ctx.sent[1][1]["text"] == "Prashant Class is starting. Record it?"
    ctx.clock.advance(3600)
    gate.set_presence(ctx, 90.0, "speaking")
    await class_reach.reach(ctx, "k3", "Prashant Class", "auto")
    assert len(ctx.sent) == 2
    assert ctx.con.execute("SELECT COUNT(*) FROM held").fetchone()[0] == 0


def A(text, kind="homework", due=None, done=0):
    return {"text": text, "kind": kind, "due": due, "done": done}


TODAY = date(2026, 11, 21)                                                       # a Saturday


def test_after_class_line_formats():
    f = class_reach.after_class_line
    assert f("Physics", [], TODAY) is None
    assert f("Physics", [A("Finish worksheet 4.", done=1)], TODAY) is None
    assert f("Physics", [A("Finish worksheet 4.", due="2026-11-26")], TODAY) == "From Physics: Finish worksheet 4 (due Thu)."
    assert f("Physics", [A("Read ch 5", "reading"), A("Do Q1-8", due="2026-11-22")], TODAY) == \
        "From Physics: Do Q1-8 (due tomorrow), and Read ch 5."
    three = [A("Bring calculator", "bring", "2026-11-23"), A("Do Q1-8", due="2026-11-24"), A("Kinematics test", "test", "2026-12-05")]
    assert f("Physics", three, TODAY) == "From Physics: Kinematics test (due 5 Dec), and Do Q1-8 (due Tue). 1 more in your notes."


@pytest.mark.asyncio
async def test_after_class_says_it_once_through_the_gate(ctx):
    s = recorded(ctx)
    ctx.con.execute("INSERT INTO class_actions(id, session_id, text, kind, due, due_text) VALUES ('ca_1', ?, 'Finish worksheet 4', 'homework', '2026-11-26', 'Thursday')", (s,))
    assert await class_reach.after_class(ctx, s) == "say"
    assert ctx.sent[0][1]["text"] == "From Physics: Finish worksheet 4 (due Thu)."
    assert await class_reach.after_class(ctx, s) == "drop"                       # already told
    empty = recorded(ctx)
    assert await class_reach.after_class(ctx, empty) is None and len(ctx.sent) == 1
