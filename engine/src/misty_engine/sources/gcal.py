"""Google Calendar reader (read-only). One item per ISO week, like chats (decision P7)."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator

from googleapiclient.errors import HttpError

from ..clock import SGT, Clock
from ..errors import Fatal, Retryable
from ..types import RawItem


def _status(e: HttpError) -> int:
    return int(getattr(e.resp, "status", 0) or 0)


def _wrap(fn):
    try:
        return fn()
    except HttpError as e:
        s = _status(e)
        if s == 410:
            raise
        if s in (401, 403):
            raise Fatal("NEEDS_AUTH")
        if s == 429 or s >= 500:
            raise Retryable(f"GCAL_{s}")
        raise


def _start(ev: dict) -> datetime:
    s = ev.get("start", {})
    if "dateTime" in s:
        return datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone(SGT)
    return datetime.fromisoformat(s["date"]).replace(tzinfo=SGT)


def _end(ev: dict) -> datetime | None:
    e = ev.get("end", {})
    if "dateTime" in e:
        return datetime.fromisoformat(e["dateTime"].replace("Z", "+00:00")).astimezone(SGT)
    return None


def week_key(d: datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def event_line(ev: dict) -> str:
    st, en = _start(ev), _end(ev)
    when = f"{st:%Y-%m-%d %H:%M}–{en:%H:%M}" if en and "dateTime" in ev.get("start", {}) else f"{st:%Y-%m-%d} all day"
    title = ev.get("summary") or "(no title)"
    loc = f" ({ev['location']})" if ev.get("location") else ""
    names = [a.get("displayName") for a in ev.get("attendees", []) if a.get("displayName") and not a.get("self")]
    who = f" [{', '.join(names)}]" if names else ""
    return f"{when}  {title}{loc}{who}"


class CalendarSource:
    name = "gcal"
    item_sources = ("gcal",)

    def __init__(self, service_factory: Callable[[], object], calendars: tuple[str, ...] = ("primary",),
                 past_months: int = 12, future_months: int = 6, clock: Clock | None = None):
        self._factory = service_factory
        self.calendars, self.past_months, self.future_months = calendars, past_months, future_months
        self.clock = clock or Clock()
        self.stats: Counter = Counter()
        self._cursor: str | None = None

    # ---- fetching
    def _events(self, svc, cal: str, **kw) -> tuple[list[dict], str | None]:
        out, token, sync = [], None, None
        while True:
            r = _wrap(lambda: svc.events().list(calendarId=cal, singleEvents=True, maxResults=2500,
                                                pageToken=token, **kw).execute())
            out += r.get("items", [])
            token = r.get("nextPageToken")
            sync = r.get("nextSyncToken") or sync
            if not token:
                return out, sync

    def _week_events(self, svc, cal: str, week: str) -> list[dict]:
        y, w = week.split("-W")
        mon = datetime.fromisocalendar(int(y), int(w), 1).replace(tzinfo=SGT)
        ev, _ = self._events(svc, cal, orderBy="startTime", timeMin=mon.isoformat(),
                             timeMax=(mon + timedelta(days=7)).isoformat())
        return ev

    def _full(self, svc, cal: str) -> tuple[dict[str, list[dict]], str | None]:
        now = datetime.fromtimestamp(self.clock.now(), timezone.utc)
        ev, sync = self._events(svc, cal, orderBy="startTime",
                                timeMin=(now - timedelta(days=30 * self.past_months)).isoformat(),
                                timeMax=(now + timedelta(days=30 * self.future_months)).isoformat())
        weeks: dict[str, list[dict]] = defaultdict(list)
        for e in ev:
            if e.get("status") != "cancelled":
                weeks[week_key(_start(e))].append(e)
        return weeks, sync

    def _item(self, cal: str, week: str, events: list[dict]) -> RawItem:
        text = "\n".join(event_line(e) for e in sorted(events, key=_start))
        return RawItem(source="gcal", kind="calendar_week", locator=f"gcal:{cal}:{week}", title=f"Calendar {week}",
                       meta={"rev": hashlib.sha256(text.encode()).hexdigest()[:16], "week": week, "calendar": cal})

    def discover(self, cursor: str | None, limit: int | None = None) -> Iterator[RawItem]:
        svc = self._factory()
        tokens: dict[str, str] = {}
        if cursor:
            try:
                import json
                tokens = json.loads(cursor)
            except ValueError:
                tokens = {}
        new_tokens: dict[str, str] = {}
        n = 0
        for cal in self.calendars:
            tok = tokens.get(cal)
            weeks: dict[str, list[dict]] | None = None
            if tok:
                try:
                    changed, sync = self._events(svc, cal, syncToken=tok)
                    new_tokens[cal] = sync or tok
                    touched = {week_key(_start(e)) for e in changed if "start" in e}
                    weeks = {w: [e for e in self._week_events(svc, cal, w) if e.get("status") != "cancelled"]
                             for w in sorted(touched)}
                except HttpError as e:
                    if _status(e) != 410:
                        raise
                    weeks = None                       # token expired: full resync below
            if weeks is None:
                weeks, sync = self._full(svc, cal)
                if sync:
                    new_tokens[cal] = sync
            for week, events in sorted(weeks.items()):
                if limit and n >= limit:
                    break
                n += 1
                self.stats["weeks"] += 1
                yield self._item(cal, week, events)
        import json
        self._cursor = json.dumps(new_tokens, sort_keys=True) if new_tokens else cursor

    def next_cursor(self) -> str | None:
        return self._cursor

    def load_text(self, item: RawItem) -> str:
        svc = self._factory()
        events = [e for e in self._week_events(svc, item.meta["calendar"], item.meta["week"])
                  if e.get("status") != "cancelled"]
        return "\n".join(event_line(e) for e in sorted(events, key=_start))
