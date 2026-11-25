import json

from fakes_google import FakeCal, make_event
from misty_engine.sources.gcal import CalendarSource, week_key

EVENTS = [
    make_event("e1", "Physics lesson", "2026-10-12T09:00:00+08:00", "2026-10-12T10:30:00+08:00", location="Room 3"),
    make_event("e2", "iGEM sync", "2026-10-14T16:00:00+08:00", "2026-10-14T17:00:00+08:00",
               description="SECRET agenda", attendees=[{"displayName": "Aarav Shah", "email": "a@x.com"}]),
    make_event("e3", "Dentist", "2026-10-20T11:00:00+08:00", "2026-10-20T12:00:00+08:00"),
    make_event("e4", "Cancelled thing", "2026-10-13T11:00:00+08:00", "2026-10-13T12:00:00+08:00", status="cancelled"),
]


def test_gcal_groups_by_week_and_incremental_refetches_week(clock):
    from datetime import datetime, timezone
    clock.t = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()
    cal = FakeCal(list(EVENTS))
    src = CalendarSource(cal.factory, clock=clock)
    items = list(src.discover(None))
    assert sorted(i.locator for i in items) == ["gcal:primary:2026-W42", "gcal:primary:2026-W43"]
    cur = src.next_cursor()
    assert json.loads(cur) == {"primary": "sync1"}
    text = src.load_text(next(i for i in items if i.meta["week"] == "2026-W42"))
    assert "Physics lesson (Room 3)" in text and "[Aarav Shah]" in text
    assert "SECRET" not in text and "a@x.com" not in text and "Cancelled thing" not in text
    # an edit in week 43 arrives through the sync token; the whole week is refetched
    cal.events_data.append(make_event("e5", "Cricket nets", "2026-10-21T17:00:00+08:00", "2026-10-21T18:00:00+08:00"))
    cal.changed = [cal.events_data[-1]]
    cal.version = 2
    src2 = CalendarSource(cal.factory, clock=clock)
    out = list(src2.discover(cur))
    assert [i.locator for i in out] == ["gcal:primary:2026-W43"]
    assert out[0].meta["rev"] != next(i for i in items if i.meta["week"] == "2026-W43").meta["rev"]
    assert any(c["syncToken"] == "sync1" and c["timeMin"] is None for c in cal.calls)
    assert json.loads(src2.next_cursor()) == {"primary": "sync2"}


def test_gcal_410_resyncs(clock):
    from datetime import datetime, timezone
    clock.t = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()
    cal = FakeCal(list(EVENTS))
    cal.expire = True
    src = CalendarSource(cal.factory, clock=clock)
    out = list(src.discover(json.dumps({"primary": "old"})))
    assert len(out) == 2 and src.next_cursor() is not None


def test_week_key_is_iso():
    from datetime import datetime
    assert week_key(datetime(2026, 10, 12)) == "2026-W42"
