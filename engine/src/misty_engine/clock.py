"""Time helpers. Everything schedule-related is in Asia/Singapore."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")


class Clock:
    def now(self) -> float:
        return time.time()


class FakeClock(Clock):
    def __init__(self, t: float):
        self.t = t

    def now(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def sgt_day_start(ts: float) -> float:
    d = datetime.fromtimestamp(ts, SGT)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def next_sgt_midnight(ts: float) -> float:
    start = datetime.fromtimestamp(sgt_day_start(ts), SGT)
    return (start + timedelta(days=1)).timestamp()


def sgt_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, SGT).date().isoformat()


def next_sgt_time(ts: float, hour: int, minute: int, weekday: int | None = None) -> float:
    """Next wall-clock hh:mm in SGT strictly after ts; weekday 0=Mon..6=Sun or None for daily."""
    now = datetime.fromtimestamp(ts, SGT)
    cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if weekday is None:
        return (cand if cand > now else cand + timedelta(days=1)).timestamp()
    cand += timedelta(days=(weekday - now.weekday()) % 7)
    return (cand if cand > now else cand + timedelta(days=7)).timestamp()
