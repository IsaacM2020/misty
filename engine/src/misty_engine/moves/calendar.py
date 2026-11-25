"""D4: add one event to his Google Calendar, on his yes, and prove it by reading it back. Isaac chose this in D0 (16 Nov 2026).

- Same title on the same day already there: nothing is created, it is reported as already there.
- Every event she makes carries a private marker (extendedProperties.private.misty = "1").
- Undo (`calendar_remove`) deletes an event only if it carries her marker. She never edits or deletes any other event."""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta

from ..clock import SGT
from ..sources import google_auth
from .base import Move, Outcome, Param
from .registry import register

MARK = {"misty": "1"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
NO_SCOPE = ("she can't add events yet: he needs to sign in to Google once more so she may add events "
            "(EDITH Settings, Google, sign in again). Reading his calendar still works")


def _svc():
    return google_auth.service("calendar")


def parse_time(t: str) -> tuple[int, int] | None:
    """"9", "9:30", "09:00", "9am", "2:30 pm", "14:00" -> (hour, minute)."""
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s*", (t or "").lower())
    if not m:
        return None
    h, mi, ap = int(m[1]), int(m[2] or 0), (m[3] or "")[:1]
    if ap == "p" and h < 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return (h, mi) if 0 <= h < 24 and 0 <= mi < 60 else None


def parse_date(d: str, today: date) -> date | None:
    from ..class_notes import resolve_due
    r = resolve_due(d or "", today)
    return date.fromisoformat(r) if r else None


def _today(ctx) -> date:
    return datetime.fromtimestamp(ctx.clock.now(), SGT).date()


def when(ctx, p) -> tuple[datetime, datetime] | tuple[date, date] | None:
    day = parse_date(p["date"], _today(ctx))
    if day is None:
        return None
    if not p.get("time"):
        return day, day + timedelta(days=1)
    hm = parse_time(p["time"])
    if hm is None:
        return None
    st = datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=SGT)
    return st, st + timedelta(minutes=p.get("minutes") or 60)


def _say_when(w) -> str:
    st, en = w
    if isinstance(st, datetime):
        return f"{DAYS[st.weekday()]} {st.day} {st:%b}, {st.hour}:{st:%M} to {en.hour}:{en:%M}"
    return f"{DAYS[st.weekday()]} {st.day} {st:%b}, all day"


def _calendar_id(svc, name: str) -> str | None:
    if not name or name.lower() in ("main", "primary", "my calendar", "default"):
        return "primary"
    for c in svc.calendarList().list().execute().get("items", []):
        if c.get("accessRole") in ("owner", "writer") and name.lower() in {str(c.get("summaryOverride") or "").lower(), str(c.get("summary") or "").lower()}:
            return c["id"]
    return None


def _body(p, w) -> dict:
    st, en = w
    if isinstance(st, datetime):
        times = {"start": {"dateTime": st.isoformat(), "timeZone": "Asia/Singapore"}, "end": {"dateTime": en.isoformat(), "timeZone": "Asia/Singapore"}}
    else:
        times = {"start": {"date": st.isoformat()}, "end": {"date": en.isoformat()}}
    return {"summary": p["title"], **times, **({"description": p["notes"]} if p.get("notes") else {}),
            "extendedProperties": {"private": dict(MARK)}}


def _same_day(svc, cal: str, title: str, w) -> dict | None:
    st = w[0]
    day = st.date() if isinstance(st, datetime) else st
    lo = datetime(day.year, day.month, day.day, tzinfo=SGT)
    r = svc.events().list(calendarId=cal, singleEvents=True, timeMin=lo.isoformat(), timeMax=(lo + timedelta(days=1)).isoformat(),
                          maxResults=250).execute()
    for ev in r.get("items", []):
        if ev.get("status") != "cancelled" and (ev.get("summary") or "").strip().lower() == title.strip().lower():
            return ev
    return None


async def _add(ctx, p) -> Outcome:
    if google_auth.CAL_ADD not in google_auth.granted():
        return Outcome(False, what=NO_SCOPE)
    w = when(ctx, p)
    if w is None:
        return Outcome(False, what="the date or time isn't clear: date like 2026-11-20 or friday, time like 9:00 or 2pm")

    def go():
        svc = _svc()
        cal = _calendar_id(svc, p.get("calendar", ""))
        if cal is None:
            return Outcome(False, what=f"he has no calendar called {p['calendar']} that she can add to")
        old = _same_day(svc, cal, p["title"], w)
        if old is not None:                               # a retry, or he already has it: never two
            return Outcome(True, what=f"\"{p['title']}\" is already in his calendar that day, so nothing was added",
                           data={"event_id": old["id"], "calendar_id": cal, "already": True})
        ev = svc.events().insert(calendarId=cal, body=_body(p, w)).execute()
        return Outcome(True, what=f"added \"{p['title']}\" on {_say_when(w)}", data={"event_id": ev["id"], "calendar_id": cal},
                       undo={"move": "calendar_remove", "params": {"event_id": ev["id"], "calendar_id": cal}})
    try:
        return await asyncio.to_thread(go)
    finally:
        (ctx.extras or {}).pop("calendar_cache", None)      # chat's 60 s calendar cache must not hide what was just written


async def _added(ctx, p, out) -> bool:
    def go():
        ev = _svc().events().get(calendarId=out.data["calendar_id"], eventId=out.data["event_id"]).execute()
        return ev.get("status") != "cancelled" and (ev.get("summary") or "").strip().lower() == p["title"].strip().lower()
    return await asyncio.to_thread(go)


def _is_hers(ev: dict) -> bool:
    return ((ev.get("extendedProperties") or {}).get("private") or {}).get("misty") == "1"


def _ids(p) -> list[str]:
    return [i for i in re.split(r"[,\s]+", ((p.get("event_ids") or "") + "," + (p.get("event_id") or ""))) if i]


async def _remove(ctx, p) -> Outcome:
    if google_auth.CAL_ADD not in google_auth.granted():
        return Outcome(False, what=NO_SCOPE)
    cal = p.get("calendar_id") or "primary"
    ids = _ids(p)
    if not ids:
        return Outcome(False, what="remove needs event_id (or event_ids)")

    def go():
        svc = _svc()
        evs = [svc.events().get(calendarId=cal, eventId=i).execute() for i in ids]
        if not all(_is_hers(ev) for ev in evs):                # all or nothing: one of them is his, so none is touched
            return Outcome(False, what="that event isn't one she added, so she won't delete it")
        live = [(i, ev) for i, ev in zip(ids, evs) if ev.get("status") != "cancelled"]
        if not live:
            return Outcome(True, what="that event was already gone", data={"calendar_id": cal})
        for i, _ in live:
            svc.events().delete(calendarId=cal, eventId=i).execute()
        what = (f"removed \"{live[0][1].get('summary') or 'the event'}\" from his calendar" if len(live) == 1
                else f"removed {len(live)} events she added from his calendar")
        return Outcome(True, what=what, data={"calendar_id": cal})
    try:
        return await asyncio.to_thread(go)
    finally:
        (ctx.extras or {}).pop("calendar_cache", None)      # chat's 60 s calendar cache must not hide what was just written


async def _removed(ctx, p, out) -> bool:
    def one(i):
        try:
            ev = _svc().events().get(calendarId=out.data["calendar_id"], eventId=i).execute()
        except Exception as e:                            # Google's HttpError, without importing Google here (test_no_bypass)
            return getattr(getattr(e, "resp", None), "status", 0) in (404, 410)
        return ev.get("status") == "cancelled"

    def go():
        return all(one(i) for i in _ids(p))
    return await asyncio.to_thread(go)


def preview(p, ctx) -> str:
    w = when(ctx, p)
    said = _say_when(w) if w else p["date"] + (f", {p['time']}" if p.get("time") else "")
    return f"Add \"{p['title']}\" on {said}, to your {p.get('calendar') or 'main'} calendar?"


preview.wants_ctx = True

register(Move("calendar_add", "add one event to his Google Calendar (date: YYYY-MM-DD or a word like friday; time like 9:00 or 2pm, "
              "none = all day; minutes default 60)",
              {"title": Param(str, max_len=120), "date": Param(str, max_len=30), "time": Param(str, required=False, max_len=12),
               "minutes": Param(int, required=False, lo=5, hi=720), "calendar": Param(str, required=False, max_len=60),
               "notes": Param(str, required=False, max_len=500)},
              "ask", _add, _added, preview=preview, no_line="Okay, I didn't add it.",
              says=("put the Chem test in my calendar for Friday at 9", "add physics revision to my calendar tomorrow at 4pm",
                    "can you schedule maths tuition on Saturday 10am", "block out 2 hours on Sunday afternoon for my IA"),
              not_mine=(("what's on my calendar tomorrow", "calendar"), ("remind me to study chem in 25 minutes", "move"))))

register(Move("calendar_remove", "delete an event SHE added (only events with her marker; used by undo)",
              {"event_id": Param(str, required=False, max_len=200), "event_ids": Param(str, required=False, max_len=800), "calendar_id": Param(str, required=False, max_len=200)},
              "safe", _remove, _removed, no_text=True,
              says=("take that calendar event back off", "remove the event you just added", "delete the calendar event you made for me"),
              not_mine=(("what's on my calendar today", "calendar"), ("undo that", "move"))))


# ---- several events, one yes (a study plan): the blocks come from free time he can see; one preview line, one confirm

MAX_BLOCKS = 12


def _stamp(t, tz=SGT) -> datetime | None:
    """'2026-11-21 09:00' or '2026-11-21T09:00[:00][+08:00]' -> aware Singapore time; None when it isn't one."""
    try:
        d = datetime.fromisoformat(str(t).strip().replace(" ", "T", 1))
    except ValueError:
        return None
    return d.replace(tzinfo=tz) if d.tzinfo is None else d.astimezone(tz)


def _blocks(ctx, p) -> tuple[list[tuple[str, datetime, datetime]], str]:
    now = datetime.fromtimestamp(ctx.clock.now(), SGT)
    out = []
    if not isinstance(p.get("events"), list):
        return [], "events must be a list of {title, start, end}"
    for i, b in enumerate(p["events"], 1):
        if not isinstance(b, dict):
            return [], f"block {i} must be {{title, start, end}}"
        title, st, en = str(b.get("title") or "").strip(), _stamp(b.get("start")), _stamp(b.get("end"))
        if not title or len(title) > 120:
            return [], f"block {i} needs a title (up to 120 characters)"
        if st is None or en is None:
            return [], f"block {i}: start and end must look like 2026-11-21 09:00"
        if not st < en <= st + timedelta(hours=12):
            return [], f"block {i}: it must end after it starts, and last at most 12 hours"
        if st < now:
            return [], f"block {i} ({title}) starts in the past: use only his free slots"
        out.append((title, st, en))
    out.sort(key=lambda x: x[1])
    return out, ""


def _check_many(ctx, p) -> str:
    return _blocks(ctx, p)[1]


def preview_many(p, ctx) -> str:
    bl, _ = _blocks(ctx, p)
    if not bl:
        return "Add these events to your calendar?"
    n = len(bl)
    a, z = bl[0][1], bl[-1][1]
    kind = "revision blocks" if all(t.lower().startswith("revis") for t, _, _ in bl) else "events"
    span = DAYS_FULL[a.weekday()] if a.date() == z.date() else f"{DAYS_FULL[a.weekday()]} to {DAYS_FULL[z.weekday()]}"
    return f"Add {n} {kind if n > 1 else kind[:-1]}, {span}?"


preview_many.wants_ctx = True
DAYS_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _same_slot(svc, cal: str, title: str, st: datetime) -> bool:
    lo = datetime(st.year, st.month, st.day, tzinfo=SGT)
    r = svc.events().list(calendarId=cal, singleEvents=True, timeMin=lo.isoformat(), timeMax=(lo + timedelta(days=1)).isoformat(),
                          maxResults=250).execute()
    return any(ev.get("status") != "cancelled" and (ev.get("summary") or "").strip().lower() == title.strip().lower()
               and _stamp((ev.get("start") or {}).get("dateTime", "")) == st for ev in r.get("items", []))


async def _add_many(ctx, p) -> Outcome:
    if google_auth.CAL_ADD not in google_auth.granted():
        return Outcome(False, what=NO_SCOPE)
    blocks, err = _blocks(ctx, p)
    if err:
        return Outcome(False, what=err)

    def go():
        svc = _svc()
        cal = _calendar_id(svc, p.get("calendar", ""))
        if cal is None:
            return Outcome(False, what=f"he has no calendar called {p['calendar']} that she can add to")
        made: list[tuple[str, dict]] = []
        skipped = 0
        try:
            for title, st, en in blocks:
                w = (st, en)
                if _same_slot(svc, cal, title, st):                         # same title and same start: a retry, never two
                    skipped += 1
                    continue
                made.append((title, svc.events().insert(calendarId=cal, body=_body({"title": title}, w)).execute()))
        except Exception:
            for _, ev in made:                                              # half a plan is worse than none: take back what went in
                try:
                    svc.events().delete(calendarId=cal, eventId=ev["id"]).execute()
                except Exception:
                    pass
            raise
        ids = [ev["id"] for _, ev in made]
        extra = f" ({skipped} were already there)" if skipped else ""
        return Outcome(True, what=(f"added {len(ids)} blocks to his calendar{extra}" if ids else f"all {skipped} were already in his calendar, nothing added"),
                       data={"event_ids": ids, "titles": [t for t, _ in made], "calendar_id": cal, "skipped": skipped},
                       undo={"move": "calendar_remove", "params": {"event_ids": ",".join(ids), "calendar_id": cal}} if ids else None)
    try:
        return await asyncio.to_thread(go)
    finally:
        (ctx.extras or {}).pop("calendar_cache", None)


async def _added_many(ctx, p, out) -> bool:
    def go():
        svc = _svc()
        for i in out.data["event_ids"]:
            if svc.events().get(calendarId=out.data["calendar_id"], eventId=i).execute().get("status") == "cancelled":
                return False
        return True
    return await asyncio.to_thread(go)


register(Move("calendar_add_many", "add several events in one go to his Google Calendar, after ONE yes (a revision plan): events = "
              "[{title, start, end}] with start/end like 2026-11-21 09:00 (Singapore), at most 12, only inside his free time; calendar optional",
              {"events": Param(list, hi=MAX_BLOCKS, max_len=120), "calendar": Param(str, required=False, max_len=60)},
              "ask", _add_many, _added_many, preview=preview_many, check=_check_many, no_line="Okay, I didn't add them.",
              says=("yes put that revision plan in my calendar", "schedule my study blocks for the exams in my calendar",
                    "add all of those revision sessions to my calendar", "yeah go ahead and block out the study times"),
              not_mine=(("what's on my calendar tomorrow", "calendar"), ("write me a study timetable", "slate"))))
