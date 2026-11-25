"""Now: one small block in the per-turn tail so she always knows his situation (what he is on, what is on now and next,
whether a class is recording, what is coming up). It only READS: the calendar comes from a cache that a scheduler job refreshes
every 2 minutes, so a turn never waits on Google. Whatever is missing leaves its line out; nothing here ever raises."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime, timedelta

from . import activity, calendar_now
from .clock import SGT, sgt_date
from .errors import Fatal
from .logs import log_event

HEAD = "Now (live data, not instructions):"
CAP = 600
CACHE_KEY = "calendar_cache"          # the same dict chat's calendar tool uses; moves/calendar pops it after a write
WINDOW = "window"                     # entry (taken_at, events, start_iso): today and the next 14 days in one read
WINDOW_DAYS = 15
FRESH_S = 300                         # the Calendar line is left out when the events are older than this
REFRESH_S = 120
COMING_DAYS = 14
DUE_DAYS = 7
MAX_COMING = 3
MAX_NEXT = 2
_BIG = re.compile(r"\b(exam|paper|SA-?\d|test|quiz)", re.I)
_STUDY = re.compile(r"^\s*revise\b", re.I)          # the study blocks she adds ("Revise: Physics HL P1") are not the exam


# ---- the shared events cache
def _cache(ctx) -> dict:
    if ctx.extras is None:
        ctx.extras = {}
    return ctx.extras.setdefault(CACHE_KEY, {})


def cached(ctx, max_age: float = FRESH_S) -> list[dict] | None:
    """The window's events if they were read today (Singapore) and less than `max_age` seconds ago, else None."""
    hit = (ctx.extras or {}).get(CACHE_KEY, {}).get(WINDOW)
    if hit is None or time.monotonic() - hit[0] > max_age or hit[2] != sgt_date(ctx.clock.now()):
        return None
    return hit[1]


def window_slice(ctx, start: date, days: int, max_age: float) -> list[dict] | None:
    """The events for [start, start+days) taken from the window when it is fresh and covers them, else None (the caller reads Google)."""
    evs = cached(ctx, max_age)
    first = date.fromisoformat(sgt_date(ctx.clock.now()))
    if evs is None or start < first or start + timedelta(days=days) > first + timedelta(days=WINDOW_DAYS):
        return None
    lo = datetime(start.year, start.month, start.day, tzinfo=SGT)
    hi = lo + timedelta(days=days)
    out = []
    for e in evs:
        end = e["end"] or (e["start"] + timedelta(days=1) if e["all_day"] else e["start"])
        if e["start"] < hi and (end > lo or (end == e["start"] and e["start"] >= lo)):
            out.append(e)
    return out


def _fetch(start: date, days: int) -> list[dict]:
    from .sources import google_auth
    svc = google_auth.service("calendar")
    hf = google_auth.http_factory(svc)
    return calendar_now.events_between(svc, start, days, http_factory=hf) if hf else calendar_now.events_between(svc, start, days)


async def refresh(ctx) -> bool:
    """One Google read for today + the next 14 days into the shared cache. False (and the old cache stays) if it could not."""
    if not ctx.net.online:
        return False
    start = date.fromisoformat(sgt_date(ctx.clock.now()))
    try:
        evs = await asyncio.to_thread(_fetch, start, WINDOW_DAYS)
    except Fatal:                                                   # not connected: nothing to show, nothing to log every 2 minutes
        return False
    except Exception as e:                                          # HttpError, timeouts
        log_event("NOW_CALENDAR_FAILED", err=type(e).__name__)
        return False
    _cache(ctx)[WINDOW] = (time.monotonic(), evs, start.isoformat())
    return True


# ---- the lines
def _mins(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} min" if m < 60 else f"{m // 60}h" + (f" {m % 60}m" if m % 60 else "")


def _on_line(ctx, frame, now: float) -> str:
    if frame is None:
        return ""
    if frame.private:
        return "On: a private app"
    app, title = frame.app.strip(), frame.title.strip()[:80]
    what = f"{app} — {title}" if app and title and title != app else (app or title)
    if not what:
        return ""
    try:
        st = activity.current_stretch(ctx, now)
    except Exception:
        st = None
    if st and (not frame.bundle_id or st["bundle"] == frame.bundle_id) and st["seconds"] >= 120:
        what += f" (for {_mins(st['seconds'])})"
    return "On: " + what


def _short(e: dict) -> dict:
    return {**e, "title": e["title"][:50], "location": e["location"][:30], "link": None}


def _calendar(ctx, now: datetime) -> tuple[str, list[dict]]:
    evs = cached(ctx)
    if not evs:
        return "", []
    today = [e for e in evs if not e["all_day"] and e["start"].date() == now.date()]
    on = [e for e in today if e["start"] <= now < (e["end"] or e["start"])]
    nxt = [e for e in today if e["start"] > now][:MAX_NEXT]
    pick = on[-1:] + nxt
    if not pick:
        return "", []
    return "Calendar: " + "; ".join(calendar_now.lines([_short(e) for e in pick], multi_day=False, now=now)), pick


def _class_line(ctx, ts: float) -> str:
    try:
        r = ctx.con.execute("SELECT subject, title, mode, status, started_at FROM class_sessions WHERE status IN ('live','paused') "
                            "ORDER BY started_at DESC LIMIT 1").fetchone()
    except Exception:
        return ""
    if r is None:
        return ""
    name = (r["subject"] or r["title"] or "a class")[:40]
    bits = [r["mode"] or "room", f"{_mins(ts - r['started_at'])} in"] + (["paused"] if r["status"] == "paused" else [])
    return f"Class: Recording {name} ({', '.join(bits)})"


def _coming(ctx, evs: list[dict] | None, shown: list[dict], now: datetime) -> str:
    today = now.date()
    items: list[tuple[date, int, str]] = []
    for e in evs or []:
        d = e["start"].date()
        if e in shown or not 0 <= (d - today).days <= COMING_DAYS or not _BIG.search(e["title"]) or _STUDY.search(e["title"]):
            continue
        if not e["all_day"] and (e["end"] or e["start"]) <= now:
            continue
        n = (d - today).days
        when = "today" if n == 0 else "tomorrow" if n == 1 else f"in {n}d"
        t = "" if e["all_day"] else f" {e['start']:%H:%M}"
        items.append((d, 0, f"{e['title'][:50]} {d:%a}{t} ({when})"))
    try:
        lo = (today - timedelta(days=DUE_DAYS)).isoformat()
        hi = (today + timedelta(days=DUE_DAYS)).isoformat()
        rows = ctx.con.execute("SELECT text, kind, due FROM class_actions WHERE done=0 AND due IS NOT NULL AND due>=? AND due<=? "
                               "ORDER BY due LIMIT 6", (lo, hi)).fetchall()
    except Exception:
        rows = []
    for r in rows:
        try:
            d = date.fromisoformat(r["due"])
        except ValueError:
            continue
        late = " (overdue)" if d < today else ""
        items.append((d, 1, f"{r['kind'].capitalize()}: {' '.join(r['text'].split())[:50]} due {d:%a}{late}"))
    items.sort(key=lambda x: (x[0], x[1]))
    return "Coming up: " + "; ".join(x[2] for x in items[:MAX_COMING]) if items else ""


def block(ctx, frame, now: float) -> str:
    """"" or the Now block: a header and up to 4 short lines, never more than 600 characters. Safe to call on every turn."""
    sg = datetime.fromtimestamp(now, SGT)
    evs = cached(ctx)
    cal, shown = "", []
    try:
        cal, shown = _calendar(ctx, sg)
    except Exception as e:
        log_event("NOW_BLOCK_FAILED", part="calendar", err=type(e).__name__)
    parts = []
    for name, fn in (("on", lambda: _on_line(ctx, frame, now)), ("class", lambda: _class_line(ctx, now)),
                     ("coming", lambda: _coming(ctx, evs, shown, sg))):
        try:
            parts.append((name, fn()))
        except Exception as e:
            log_event("NOW_BLOCK_FAILED", part=name, err=type(e).__name__)
    by = dict(parts)
    lines = [x for x in (by.get("on"), by.get("class"), cal, by.get("coming")) if x]     # the order he reads them; the drop order is the reverse
    while lines and len("\n".join([HEAD] + lines)) > CAP:
        lines.pop()
    return "\n".join([HEAD] + lines) if lines else ""
