from datetime import datetime, timezone

from misty_engine.clock import FakeClock, SGT, next_sgt_midnight, next_sgt_time, sgt_day_start
from misty_engine.errors import NetworkDown, Retryable
from misty_engine.types import approx_tokens, fingerprint, item_id_for, new_id


def test_item_id_stable_and_prefixed():
    a = item_id_for("folders", "/Users/me/Desktop/a.pdf")
    assert a == item_id_for("folders", "/Users/me/Desktop/a.pdf")
    assert a.startswith("it_") and len(a) == 19
    assert a != item_id_for("gmail", "/Users/me/Desktop/a.pdf")


def test_fingerprint_ignores_whitespace_only_changes():
    assert fingerprint("a  b\n\nc") == fingerprint("a b c")
    assert fingerprint("a b c") != fingerprint("a b d")


def test_new_id_format():
    j = new_id("jb")
    assert j.startswith("jb_") and len(j) == 19


def test_approx_tokens():
    assert approx_tokens("") == 0 and approx_tokens("abcd") == 1 and approx_tokens("abcde") == 2


def test_sgt_day_boundaries():
    t = datetime(2026, 10, 14, 15, 59, tzinfo=timezone.utc).timestamp()  # 23:59 SGT
    assert datetime.fromtimestamp(sgt_day_start(t), SGT).hour == 0
    assert next_sgt_midnight(t) - t == 60


def test_next_sgt_time_weekly_sunday_2000():
    t = datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc).timestamp()  # Wed 12:00 SGT
    n = datetime.fromtimestamp(next_sgt_time(t, 20, 0, weekday=6), SGT)
    assert (n.weekday(), n.hour, n.minute, n.day) == (6, 20, 0, 18)


def test_errors_stringify_to_code_only():
    assert str(Retryable("HTTP_503")) == "HTTP_503"
    assert NetworkDown().code == "NETWORK_DOWN"


def test_fake_clock_advances():
    c = FakeClock(10.0)
    c.advance(5)
    assert c.now() == 15.0
