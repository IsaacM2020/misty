"""The chat's calendar tool: Isaac's Google Calendar, live, every calendar he has on; never Apple's, never the notes."""
import json
from datetime import timedelta, date, datetime

import pytest

from misty_engine import calendar_now, chat
from misty_engine.errors import Fatal
from misty_engine.sources import google_auth

from test_chat import ScriptBrain, call, say


class FakeCal:
    """Stands in for the Google client: calendarList().list() and events().list() as the real one chains them."""
    def __init__(self, cals, events):
        self.cals, self.events_by_cal, self.asked = cals, events, []

    def calendarList(self):
        outer = self
        class L:
            def list(self_):
                return type("R", (), {"execute": lambda s: {"items": outer.cals}})()
        return L()

    def events(self):
        outer = self
        class E:
            def list(self_, **kw):
                outer.asked.append(kw)
                return type("R", (), {"execute": lambda s: {"items": outer.events_by_cal.get(kw["calendarId"], [])}})()
        return E()


def ev(title, start, end=None, **kw):
    e = {"summary": title, "start": {"dateTime": start}, "end": {"dateTime": end or start}}
    e.update(kw)
    return e


CALS = [{"id": "primary", "summary": "Isaac", "selected": True, "accessRole": "owner"},
        {"id": "school", "summary": "School", "accessRole": "reader"},
        {"id": "hidden", "summary": "Off", "selected": False}]
EVENTS = {
    "primary": [ev("Physics class", "2026-10-24T14:00:00+08:00", "2026-10-24T15:00:00+08:00",
                   hangoutLink="https://meet.google.com/abc-defg-hij"),
                ev("Skipped thing", "2026-10-24T09:00:00+08:00", attendees=[{"self": True, "responseStatus": "declined"}]),
                ev("Cancelled", "2026-10-24T10:00:00+08:00", status="cancelled")],
    "school": [{"summary": "Sports day", "start": {"date": "2026-10-24"}, "end": {"date": "2026-10-25"}},
               ev("Maths", "2026-10-24T08:00:00+08:00", "2026-10-24T09:00:00+08:00", location="Room 4")],
    "hidden": [ev("Should not show", "2026-10-24T12:00:00+08:00")],
}


def test_events_come_from_every_visible_calendar_in_order_and_skip_declined_cancelled_hidden():
    out = calendar_now.events_between(FakeCal(CALS, EVENTS), date(2026, 10, 24), 1)
    assert [e["title"] for e in out] == ["Sports day", "Maths", "Physics class"]
    assert calendar_now.lines(out, multi_day=False) == [
        "all day Sports day", "08:00-09:00 Maths (Room 4)", "14:00-15:00 Physics class (has a video link)"]
    assert out[2]["link"] == "https://meet.google.com/abc-defg-hij"


def test_the_window_is_the_singapore_day():
    cal = FakeCal(CALS, EVENTS)
    calendar_now.events_between(cal, date(2026, 10, 24), 2)
    assert cal.asked[0]["timeMin"] == "2026-10-24T00:00:00+08:00" and cal.asked[0]["timeMax"] == "2026-10-26T00:00:00+08:00"


def test_span_words_and_limits():
    t = date(2026, 10, 24)
    assert calendar_now.span("today", 1, t) == (t, 1)
    assert calendar_now.span("Tomorrow", 3, t) == (date(2026, 10, 25), 3)
    assert calendar_now.span("2026-11-02", 99, t) == (date(2026, 11, 2), 31)
    assert calendar_now.span("", 0, t) == (t, 1)
    with pytest.raises(Fatal):
        calendar_now.span("next banana", 1, t)


@pytest.mark.asyncio
async def test_calendar_tool_answers_from_google_not_from_the_notes_or_the_mac(ctx, monkeypatch):
    monkeypatch.setattr(google_auth, "service", lambda name, creds=None: FakeCal(CALS, EVENTS))
    ctx.net.online = True
    ctx.openrouter = ScriptBrain({"calls": [call("calendar", when="2026-10-24")]}, {"content": "Physics at 2."})
    out, _, statuses = await say(ctx, "what's on my calendar")
    tool = json.loads(ctx.openrouter.bodies[1]["messages"][-1]["content"])
    assert tool["ok"] and tool["count"] == 3 and "14:00-15:00 Physics class (has a video link)" in tool["events"]
    assert tool["links"] == [{"title": "Physics class", "url": "https://meet.google.com/abc-defg-hij"}]
    assert statuses == ["Checking your Google Calendar…"] and out["actions"][0]["tool"] == "calendar"
    assert "Never answer a schedule question from his notes" in chat.SYSTEM


@pytest.mark.asyncio
async def test_calendar_tool_says_so_when_google_is_not_connected(ctx, monkeypatch):
    def boom(name, creds=None):
        raise Fatal("NEEDS_AUTH")
    monkeypatch.setattr(google_auth, "service", boom)
    ctx.net.online = True
    ctx.openrouter = ScriptBrain({"calls": [call("calendar")]}, {"content": "Google needs reconnecting."})
    out, _, _ = await say(ctx, "what's on today")
    tool = json.loads(ctx.openrouter.bodies[1]["messages"][-1]["content"])
    assert tool["ok"] is False and "Reconnect Google" in tool["error"]


def test_the_prompt_tells_her_to_act_not_offer():
    assert "Don't end with \"Want me to...?\"" in chat.SYSTEM


@pytest.mark.asyncio
async def test_open_url_opens_https_only_and_a_fact_with_its_link_in_the_sentence_opens(ctx, monkeypatch):
    from misty_engine import facts
    got = []

    async def op(target):
        got.append(target)
    monkeypatch.setattr(chat, "_open", op)

    async def nothing(_):
        return None

    async def no(_):
        return False
    r = await chat.run_tool(ctx, "open_url", {"url": "https://meet.google.com/abc-defg-hij"}, on_status=nothing, confirm=no)
    assert r["ok"] and got == ["https://meet.google.com/abc-defg-hij"]
    for bad in ("file:///etc/passwd", "http://x.com/a", "javascript:alert(1)", ""):
        assert (await chat.run_tool(ctx, "open_url", {"url": bad}, on_status=nothing, confirm=no))["ok"] is False
    facts.remember(ctx, "physics class link", "Isaac's physics class is on Meet at https://meet.google.com/xyz-abcd-efg (Prashant sir).")
    r = await chat.run_tool(ctx, "open_fact", {"key": "physics class link"}, on_status=nothing, confirm=no)
    assert r["ok"] and got[-1] == "https://meet.google.com/xyz-abcd-efg"


def test_current_class_from_calendar_picks_online_by_video_link():
    from datetime import datetime, timedelta
    from misty_engine import calendar_now
    from misty_engine.clock import SGT
    now = datetime(2026, 10, 26, 11, 25, tzinfo=SGT)
    mk = lambda title, h, m, link=None, all_day=False: {
        "start": datetime(2026, 10, 26, h, m, tzinfo=SGT), "end": datetime(2026, 10, 26, h, m, tzinfo=SGT) + timedelta(hours=1),
        "all_day": all_day, "title": title, "calendar": "", "location": "", "link": link}
    evs = [mk("Holiday", 0, 0, all_day=True), mk("Physics HL", 8, 45), mk("Chem tutor", 11, 30, "https://meet.google.com/abc-defg-hij")]
    cur = calendar_now.current(evs, now)                       # 5 min before the 11:30 class
    assert cur["title"] == "Chem tutor" and cur["online"] is True and cur["subject"] == "Chem"
    assert calendar_now.current(evs, datetime(2026, 10, 26, 14, 0, tzinfo=SGT)) is None
    inperson = calendar_now.current([mk("Math AA", 11, 30)], now)
    assert inperson["online"] is False and inperson["subject"] == "Math"
    assert cur["starts_in"] == 300 and cur["is_class"] and cur["key"].startswith("Chem tutor@2026-10-26T11:30")
    assert calendar_now.current([mk("Dentist", 11, 30, "https://meet.google.com/abc-defg-hij")], now)["is_class"] is False


def test_meeting_link_pasted_in_location_or_description_counts():
    from misty_engine.calendar_now import _link
    assert _link({"location": "https://meet.zoho.in/xquf-hfl-wcp"}) == "https://meet.zoho.in/xquf-hfl-wcp"
    assert _link({"description": "Join: https://us02web.zoom.us/j/123?pwd=abc. See you"}) == "https://us02web.zoom.us/j/123?pwd=abc"
    assert _link({"description": "Room 4B, bring your textbook", "location": "Block C"}) is None
    assert _link({"hangoutLink": "https://meet.google.com/x"}) == "https://meet.google.com/x"


def test_link_saved_as_a_fact_makes_the_class_online():
    from misty_engine.calendar_now import link_from_facts
    facts = [{"key": "maths homework", "text": "due friday", "link": None, "link_title": None},
             {"key": "english class link", "text": "Mayura maam class on Zoho Meet", "link": "https://meet.zoho.in/xquf-hfl-wcp",
              "link_title": "english class link"}]
    assert link_from_facts(facts, "English Class", "English") == "https://meet.zoho.in/xquf-hfl-wcp"
    assert link_from_facts(facts, "Math AA", "Math") is None                     # another class's link is not this one's
    only_in_text = [{"key": "spanish", "text": "class is at https://us02web.zoom.us/j/99 on mondays", "link": None, "link_title": None}]
    assert link_from_facts(only_in_text, "Spanish B", "Spanish") == "https://us02web.zoom.us/j/99"


def test_a_fact_that_says_online_counts_even_without_a_link():
    from misty_engine.calendar_now import says_online
    facts = [{"key": "physics class link", "text": "Isaac's online physics class with Prashant sir is held on Google Meet",
              "link": None, "link_title": None}]
    assert says_online(facts, "Physics HL", "Physics") is True
    assert says_online(facts, "Chem tutor", "Chem") is False
    assert says_online([{"key": "math", "text": "math class is in room 4B", "link": None, "link_title": None}], "Math AA", "Math") is False


def test_class_about_to_start_beats_a_longer_event_still_running():
    # 3 Nov: a Spanish reading block 19:00-20:00 runs into the 20:00 Physics class; at 19:58 Physics is the one
    from datetime import datetime, timedelta
    from misty_engine import calendar_now
    from misty_engine.clock import SGT
    mk = lambda title, h, mins: {"start": datetime(2026, 11, 3, h, 0, tzinfo=SGT),
                                 "end": datetime(2026, 11, 3, h, 0, tzinfo=SGT) + timedelta(minutes=mins),
                                 "all_day": False, "title": title, "calendar": "", "location": "", "link": None}
    evs = [mk("Spanish reading task - Laura Tertre", 19, 60), mk("Physics class", 20, 60), mk("Break / dinner", 21, 30)]
    cur = calendar_now.current(evs, datetime(2026, 11, 3, 19, 58, tzinfo=SGT))
    assert cur["title"] == "Physics class" and cur["subject"] == "Physics" and cur["starts_in"] == 120
    assert calendar_now.current(evs, datetime(2026, 11, 3, 19, 30, tzinfo=SGT))["title"].startswith("Spanish")
    assert calendar_now.current(evs, datetime(2026, 11, 3, 20, 58, tzinfo=SGT))["title"] == "Physics class"


def test_subject_words_match_the_start_of_a_word_not_the_middle():
    # "econ" is inside "second" and "reconnect": a non-class event must not turn into an Econ class (and beat a real one)
    from datetime import datetime, timedelta
    from misty_engine import calendar_now
    from misty_engine.clock import SGT
    g = calendar_now.guess_subject
    assert g("Second hand book sale") is None and g("Meet Sam to reconnect") is None
    assert g("Dentist, second visit") is None
    assert [g(t) for t in ("Econ class", "IB Economics HL", "Chemistry lab", "Pre-Calculus", "Maths AA", "Physics HL",
                           "English Lang Lit", "Spanish B", "Español")] == \
        ["Econ", "Econ", "Chem", "Math", "Math", "Physics", "English", "Spanish", "Spanish"]
    mk = lambda title, h: {"start": datetime(2026, 11, 3, h, 0, tzinfo=SGT), "end": datetime(2026, 11, 3, h, 0, tzinfo=SGT) + timedelta(hours=2),
                           "all_day": False, "title": title, "calendar": "", "location": "", "link": None}
    cur = calendar_now.current([mk("Second hand book fair", 20), mk("Homework block", 19)], datetime(2026, 11, 3, 20, 5, tzinfo=SGT))
    assert cur["subject"] is None and cur["is_class"] is False


def test_a_generic_word_in_the_event_title_does_not_pull_in_another_class_link():
    # "Online Physics class" must not pick up the link of his "online English class" just because both say "online"
    from misty_engine.calendar_now import link_from_facts, says_online
    english = [{"key": "english class link", "text": "My online English class is at https://us02web.zoom.us/j/111",
                "link": None, "link_title": None}]
    assert link_from_facts(english, "Online Physics class", "Physics") is None
    assert says_online(english, "Online Physics class", "Physics") is False
    task = [{"key": "english task", "text": "English class reading task is online on Zoom", "link": "https://us02web.zoom.us/j/9",
             "link_title": None}]
    assert link_from_facts(task, "Spanish reading task - Laura Tertre", "Spanish") is None
    assert says_online(task, "Spanish reading task - Laura Tertre", "Spanish") is False
    # still found by the class's own words
    assert link_from_facts(english, "Online English class", "English") == "https://us02web.zoom.us/j/111"


def test_with_a_clock_each_event_says_where_it_is_against_now():
    out = calendar_now.events_between(FakeCal(CALS, EVENTS), date(2026, 10, 24), 1)
    at_1430 = datetime.fromisoformat("2026-10-24T14:30:00+08:00")
    assert calendar_now.lines(out, multi_day=False, now=at_1430) == [
        "all day Sports day", "08:00-09:00 Maths (Room 4, done, ended 5h 30m ago)",
        "14:00-15:00 Physics class (has a video link, on now, ends 15:00)"]
    at_2330 = datetime.fromisoformat("2026-10-24T23:30:00+08:00")
    assert calendar_now.lines(out, multi_day=False, now=at_2330)[2].endswith("(has a video link, done, ended 8h 30m ago)")
    at_0700 = datetime.fromisoformat("2026-10-24T07:00:00+08:00")
    assert calendar_now.lines(out, multi_day=False, now=at_0700)[1] == "08:00-09:00 Maths (Room 4, in 1h)"
    assert "done" not in "".join(calendar_now.lines(out, multi_day=False))      # no clock, no marks (the exam path)


def test_the_afternoon_case_from_real_use():
    def ev(h1, m1, h2, m2, title):
        return {"start": datetime(2026, 11, 21, h1, m1, tzinfo=calendar_now.SGT), "end": datetime(2026, 11, 21, h2, m2, tzinfo=calendar_now.SGT),
                "all_day": False, "title": title, "location": "", "link": None}
    evs = [ev(13, 30, 15, 0, "Prashant Class"), ev(19, 0, 20, 0, "English Class"), ev(15, 30, 16, 30, "Maths")]
    now = datetime(2026, 11, 21, 15, 55, tzinfo=calendar_now.SGT)
    assert calendar_now.lines(evs, multi_day=False, now=now) == [
        "13:30-15:00 Prashant Class (done, ended 55m ago)", "19:00-20:00 English Class (in 3h 5m)",
        "15:30-16:30 Maths (on now, ends 16:30)"]
    tomorrow = ev(9, 0, 10, 0, "Chem")
    tomorrow["start"] += timedelta(days=1)
    tomorrow["end"] += timedelta(days=1)
    assert calendar_now.lines([tomorrow], multi_day=True, now=now) == ["Sun 22 Nov 09:00-10:00 Chem"]      # not today: no countdown


# ---- speed: calendars read side by side, results kept 60 s --------------------------------------------------------

class ThreadedCal(FakeCal):
    """Records which thread each events().list().execute() ran on and the http it was given."""
    def events(self):
        outer = self
        import threading, time as _t
        class E:
            def list(self_, **kw):
                outer.asked.append(kw)
                def run(http=None):
                    _t.sleep(0.2)
                    outer.seen = getattr(outer, "seen", []) + [(threading.get_ident(), http)]
                    return {"items": outer.events_by_cal.get(kw["calendarId"], [])}
                return type("R", (), {"execute": staticmethod(run)})()
        return E()


def test_calendars_are_read_side_by_side_each_with_its_own_http_and_the_result_is_unchanged():
    import time
    cal = ThreadedCal(CALS, EVENTS)
    n = iter(range(100))
    t0 = time.monotonic()
    out = calendar_now.events_between(cal, date(2026, 10, 24), 1, http_factory=lambda: f"http{next(n)}")
    took = time.monotonic() - t0
    assert [e["title"] for e in out] == ["Sports day", "Maths", "Physics class"]
    assert took < 0.35                                              # two calendars x 0.2 s ran together, not one after another
    assert len({h for _, h in cal.seen}) == 2 and len({t for t, _ in cal.seen}) == 2
    same = calendar_now.events_between(FakeCal(CALS, EVENTS), date(2026, 10, 24), 1)
    assert [e["title"] for e in same] == [e["title"] for e in out]


@pytest.mark.asyncio
async def test_calendar_tool_reuses_the_events_for_60_seconds_but_relabels_them_and_a_write_clears_it(ctx, monkeypatch):
    built = []

    def svc(name, creds=None):
        built.append(1)
        return FakeCal(CALS, EVENTS)
    monkeypatch.setattr(google_auth, "service", svc)
    ctx.net.online = True

    async def nothing(_):
        return None
    a = await chat._calendar(ctx, {"when": "2026-10-24"}, nothing)
    b = await chat._calendar(ctx, {"when": "2026-10-24"}, nothing)
    assert len(built) == 1 and a["events"] == b["events"] and b["count"] == 3
    await chat._calendar(ctx, {"when": "2026-10-25"}, nothing)       # another day is another fetch
    assert len(built) == 2
    ctx.extras.pop("calendar_cache")                                  # what moves/calendar does after an add or a remove
    await chat._calendar(ctx, {"when": "2026-10-24"}, nothing)
    assert len(built) == 3
    k = ("2026-10-24", 1)
    import time as _t
    ctx.extras["calendar_cache"][k] = (_t.monotonic() - 61, ctx.extras["calendar_cache"][k][1])
    await chat._calendar(ctx, {"when": "2026-10-24"}, nothing)
    assert len(built) == 4                                            # stale after 60 s
