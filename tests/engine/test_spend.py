from datetime import datetime, timezone

import pytest

from misty_engine.clock import FakeClock
from misty_engine.spend import SpendMeter, estimate_usd


def test_daily_cap_resets_at_sgt_midnight_with_utc_clock(con, settings, tz_utc):  # Review Focus 2
    clock = FakeClock(datetime(2026, 10, 14, 15, 59, tzinfo=timezone.utc).timestamp())  # 23:59 SGT
    m = SpendMeter(con, clock, settings)
    m.record(provider="openrouter", model="m", job_id="jb_0000000000000001", build_id=None, usd=0.19)
    assert not m.check(0.02, "live").ok
    clock.advance(120)  # 00:01 SGT
    assert m.check(0.02, "live").ok


def test_build_spend_does_not_eat_daily_cap(con, settings, clock):
    m = SpendMeter(con, clock, settings)
    m.record(provider="openrouter", model="m", job_id=None, build_id="bd_000000000001", usd=1.50)
    assert m.check(0.10, "live").ok
    d = m.check(1.60, "build", "bd_000000000001")
    assert not d.ok and d.cap == 3.0 and abs(d.spent - 1.50) < 1e-9


def test_cap_is_inclusive(con, settings, clock):
    m = SpendMeter(con, clock, settings)
    assert m.check(0.20, "live").ok and not m.check(0.2001, "live").ok


def test_estimate_uses_pinned_prices(pinned):
    assert abs(estimate_usd(pinned, "haiku", 1_000_000, 1_000_000) - 0.60) < 1e-9
    assert abs(estimate_usd(pinned, "haiku", 1_000_000, 0, cached_tokens=1_000_000) - 0.01) < 1e-9
    assert abs(estimate_usd(pinned, "jev", 1_000_000, 0) - 0.042) < 1e-9


# ---- Q1: per-purpose sub-caps

def test_record_stores_purpose(con, clock, settings):
    m = SpendMeter(con, clock, settings)
    m.record(provider="openrouter", model="x", job_id=None, build_id=None, usd=0.002, purpose="judge")
    assert con.execute("SELECT purpose FROM spend").fetchone()[0] == "judge"


def test_purpose_subcap_defers_even_under_global_cap(con, clock, settings):
    m = SpendMeter(con, clock, settings)
    m.record(provider="openrouter", model="x", job_id=None, build_id=None, usd=0.0095, purpose="judge")
    d = m.check(0.001, "live", purpose="judge")
    assert not d.ok and d.reason == "CAP_PURPOSE:judge"
    assert m.check(0.001, "live", purpose="compile").ok


def test_build_purpose_cap_is_fraction_of_build_cap(con, clock, settings):
    m = SpendMeter(con, clock, settings)
    m.record(provider="openrouter", model="x", job_id=None, build_id="b1", usd=0.29, purpose="judge")
    assert not m.check(0.02, "build", "b1", purpose="judge").ok      # 10% of $3 = $0.30


def test_core_purposes_share_one_sub_cap(con, clock, settings):
    m = SpendMeter(con, clock, settings)
    m.record(provider="openrouter", model="x", job_id=None, build_id=None, usd=0.012, purpose="areas")
    m.record(provider="openrouter", model="x", job_id=None, build_id=None, usd=0.007, purpose="about")
    assert m.spent_purpose("priorities", "live") == pytest.approx(0.019)     # areas, about, priorities = core
    d = m.check(0.002, "live", purpose="priorities")
    assert not d.ok and d.reason == "CAP_PURPOSE:core"


def test_global_cap_wins_and_keeps_its_own_reason(con, clock, settings):
    m = SpendMeter(con, clock, settings)
    m.record(provider="openrouter", model="x", job_id=None, build_id=None, usd=0.199, purpose="compile")
    d = m.check(0.005, "live", purpose="judge")
    assert not d.ok and d.reason == "CAP_DAILY"


def test_purposes_without_a_sub_cap_only_see_the_global_cap(con, clock, settings):
    m = SpendMeter(con, clock, settings)
    m.record(provider="openrouter", model="x", job_id=None, build_id=None, usd=0.15, purpose="compile")
    assert m.check(0.04, "live", purpose="compile").ok and m.check(0.04, "live", purpose=None).ok
