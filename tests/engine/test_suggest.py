import time
from datetime import datetime, timezone

import pytest

from misty_engine import suggest
from misty_engine.clock import SGT, FakeClock


@pytest.fixture
def item(add_item):
    return add_item("it_sg00000000000001")


def row(con, sid):
    return con.execute("SELECT * FROM suggestions WHERE id=?", (sid,)).fetchone()


def test_card_text_templates_and_length():
    assert suggest.card_text("reply", "Wiki sync", "aarav@x.com") == "Reply to aarav@x.com: Wiki sync"
    assert suggest.card_text("task", "x" * 300).endswith("…") and len(suggest.card_text("task", "x" * 300)) <= 140
    assert suggest.card_text("mute", "", "promo@x.com", 4, 5) == "Mute promo@x.com? You dismissed 4 of 5 cards"


def test_add_is_idempotent_per_item_and_kind(con, item, clock):
    a = suggest.add(con, item, "task", "t", "today", None, clock)
    assert a and suggest.add(con, item, "task", "t2", "now", None, clock) is None
    assert suggest.add(con, item, "reply", "r", "none", None, clock)
    assert len(suggest.list_cards(con)) == 2


def test_badge_only_for_new_urgent_cards(con, item, clock):
    sid = suggest.add(con, item, "task", "t", "now", None, clock)
    assert suggest.badge(con)
    suggest.act(con, sid, "done", None, clock)
    assert not suggest.badge(con)


def test_snooze_tonight_and_tomorrow_in_sgt(con, item, tz_utc):
    clock = FakeClock(datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc).timestamp())      # 21:00 SGT
    sid = suggest.add(con, item, "task", "t", "today", None, clock)
    suggest.act(con, sid, "snooze", "tonight", clock)
    assert row(con, sid)["snooze_until"] == clock.now() + 3600            # past 20:00 -> +1 h
    suggest.act(con, sid, "snooze", "tomorrow", clock)
    t = datetime.fromtimestamp(row(con, sid)["snooze_until"], SGT)
    assert (t.day, t.hour, t.minute) == (15, 8, 0)
    clock2 = FakeClock(datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc).timestamp())      # 14:00 SGT
    suggest.act(con, sid, "snooze", "tonight", clock2)
    assert datetime.fromtimestamp(row(con, sid)["snooze_until"], SGT).hour == 20


def test_release_snoozes_returns_cards(con, item, clock):
    sid = suggest.add(con, item, "task", "t", "today", None, clock)
    suggest.act(con, sid, "snooze", "1h", clock)
    assert suggest.list_cards(con, "new") == [] and suggest.release_snoozes(con, clock) == 0
    clock.advance(3601)
    assert suggest.release_snoozes(con, clock) == 1
    assert [c["id"] for c in suggest.list_cards(con, "new", clock)] == [sid]


def test_weekly_mutes_threshold(con, add_item, clock):
    def cards(sender, n, dismissed):
        for i in range(n):
            iid = add_item(f"it_{sender[:4]}{i:012d}")
            sid = suggest.add(con, iid, "reply", "r", "none", sender, clock)
            if i < dismissed:
                suggest.act(con, sid, "dismiss", None, clock)
    cards("four@x.com", 5, 4)      # 80% of 5 -> mute
    cards("three@x.com", 5, 3)     # 60% -> none
    cards("few@x.com", 4, 4)       # only 4 cards -> none
    assert suggest.weekly_mutes(con, clock) == ["four@x.com"]
    assert suggest.weekly_mutes(con, clock) == []                      # an open mute card exists already
    mute = con.execute("select text, kind from suggestions where kind='mute'").fetchone()
    assert mute["text"] == "Mute four@x.com? You dismissed 4 of 5 cards"
