"""What is on Isaac's Google Calendar right now, for the chat. Live from Google (not the indexed weekly notes, which
lag and merge days), every calendar he has switched on, read-only."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .clock import SGT
from .errors import Fatal

MAX_DAYS = 31
MAX_EVENTS = 160                 # a month of his calendar (~90 events); the chat caps the text it passes on


def span(when: str, days: int, today: date) -> tuple[date, int]:
    """'today', 'tomorrow' or YYYY-MM-DD, plus how many days from there (1..31)."""
    w = (when or "today").strip().lower()
    if w == "today":
        start = today
    elif w == "tomorrow":
        start = today + timedelta(days=1)
    else:
        try:
            start = date.fromisoformat(w)
        except ValueError:
            raise Fatal("BAD_PARAMS")
    return start, max(1, min(int(days or 1), MAX_DAYS))


def _when(ev: dict) -> tuple[datetime, bool]:
    s = ev.get("start", {})
    if "dateTime" in s:
        return datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone(SGT), False
    return datetime.fromisoformat(s["date"]).replace(tzinfo=SGT), True


def _end(ev: dict) -> datetime | None:
    e = ev.get("end", {})
    if "dateTime" in e:
        return datetime.fromisoformat(e["dateTime"].replace("Z", "+00:00")).astimezone(SGT)
    return None


def _declined(ev: dict) -> bool:
    return any(a.get("self") and a.get("responseStatus") == "declined" for a in ev.get("attendees", []))


# a teacher's invite often pastes the link into the location or description instead of Google's video field
_MEET_URL = re.compile(r"https://[^\s<>\"']*(?:zoom\.us|zoomgov\.com|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com"
                       r"|meet\.zoho\.[a-z.]+|webex\.com|whereby\.com|bluejeans\.com|gotomeet\.me|meet\.jit\.si)[^\s<>\"']*", re.I)


def _link(ev: dict) -> str | None:
    if ev.get("hangoutLink"):
        return ev["hangoutLink"]
    for p in (ev.get("conferenceData") or {}).get("entryPoints", []):
        if p.get("entryPointType") == "video" and p.get("uri"):
            return p["uri"]
    for field in ("location", "description"):
        if m := _MEET_URL.search(ev.get(field) or ""):
            return m.group(0).rstrip(".,)")
    return None


def _fetch_calendar(svc, cal: dict, lo: datetime, hi: datetime, http=None) -> list[dict]:
    """Every kept event of one calendar. `http` (one per thread) is passed through when given: httplib2 is not thread-safe."""
    out: list[dict] = []
    token = None
    kw = {"http": http} if http is not None else {}
    while True:
        r = svc.events().list(calendarId=cal["id"], singleEvents=True, orderBy="startTime", maxResults=250,
                              timeMin=lo.isoformat(), timeMax=hi.isoformat(), pageToken=token).execute(**kw)
        for ev in r.get("items", []):
            if ev.get("status") == "cancelled" or _declined(ev):
                continue
            st, all_day = _when(ev)
            en = _end(ev)
            out.append({"start": st, "end": en, "all_day": all_day, "title": ev.get("summary") or "(no title)",
                        "calendar": cal.get("summaryOverride") or cal.get("summary") or "",
                        "location": ev.get("location") or "", "link": _link(ev)})
        token = r.get("nextPageToken")
        if not token:
            return out


def events_between(svc, start: date, days: int, http_factory=None) -> list[dict]:
    """Every non-cancelled, non-declined event from the start of `start` for `days` days, soonest first.
    The calendars are read side by side (each network round trip is ~0.5 s, and he has several), one `http_factory()`
    per thread; without a factory they are read one after another."""
    lo = datetime(start.year, start.month, start.day, tzinfo=SGT)
    hi = lo + timedelta(days=days)
    cals = [c for c in svc.calendarList().list().execute().get("items", [])
            if not (c.get("selected") is False or c.get("accessRole") == "freeBusyReader")]
    if http_factory is not None and len(cals) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(cals), 8)) as pool:
            parts = list(pool.map(lambda c: _fetch_calendar(svc, c, lo, hi, http_factory()), cals))
    else:
        parts = [_fetch_calendar(svc, c, lo, hi) for c in cals]
    out = [e for part in parts for e in part]
    out.sort(key=lambda e: (e["start"], e["title"]))
    return out


DAY_START, DAY_END = 9, 22        # Singapore hours he can be asked to study in
MIN_SLOT, BUFFER = 45, 15         # minutes: shorter gaps are dropped; every event keeps this much clear around it
MAX_FREE = 40


def free_slots(events: list[dict], start: date, days: int, now: datetime, lo_hour: int = DAY_START, hi_hour: int = DAY_END,
               min_minutes: int = MIN_SLOT, buffer: int = BUFFER) -> list[tuple[datetime, datetime]]:
    """Free windows (Singapore time) in the `days` days from `start`, inside lo_hour..hi_hour. Timed events block their time plus
    `buffer` minutes either side; all-day events (holidays, birthdays, "exam week") block nothing. Nothing earlier than `now`
    (a window that has started is cut to begin at the next quarter hour). Windows shorter than `min_minutes` are dropped."""
    now = now.astimezone(SGT)
    pad = timedelta(minutes=buffer)
    busy = sorted(((e["start"] - pad, (e["end"] or e["start"]) + pad) for e in events if not e["all_day"]))
    out: list[tuple[datetime, datetime]] = []
    for d in range(max(1, int(days))):
        day = start + timedelta(days=d)
        lo = datetime(day.year, day.month, day.day, lo_hour, tzinfo=SGT)
        hi = datetime(day.year, day.month, day.day, hi_hour, tzinfo=SGT)
        if now > lo:
            q = (now.minute + 14) // 15 * 15 if now.second or now.microsecond or now.minute % 15 else now.minute
            lo = max(lo, now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=q))
        cur = lo
        for b0, b1 in busy:
            if b1 <= cur or b0 >= hi:
                continue
            if b0 - cur >= timedelta(minutes=min_minutes):
                out.append((cur, b0))
            cur = max(cur, b1)
        if hi - cur >= timedelta(minutes=min_minutes):
            out.append((cur, hi))
    return out


def free_lines(slots: list[tuple[datetime, datetime]]) -> list[str]:
    """'2026-11-21 09:00-12:00 (Sat, 3h)': the date is ISO so it can go straight into calendar_add_many."""
    return [f"{a:%Y-%m-%d} {a:%H:%M}-{b:%H:%M} ({a:%a}, {_span((b - a).total_seconds())})" for a, b in slots[:MAX_FREE]]


def _span(seconds: float) -> str:
    """'25m', '1h', '3h 5m': how far away a time is, rounded to 5 minutes once it is an hour or more."""
    m = max(0, round(seconds / 60))
    if m < 60:
        return f"{m}m"
    h, r = divmod(round(m / 5) * 5, 60)
    return f"{h}h" + (f" {r}m" if r else "")


def _relative(e: dict, now: datetime) -> str | None:
    """Where an event sits against `now`: "done, ended 55m ago", "on now, ends 16:30", "in 3h" (today only)."""
    end = e["end"] or e["start"]
    if end <= now:
        return f"done, ended {_span((now - end).total_seconds())} ago" if now - end < timedelta(hours=24) else "done"
    if e["start"] <= now:
        return f"on now, ends {end:%H:%M}"
    if e["start"].date() == now.date():
        return f"in {_span((e['start'] - now).total_seconds())}"
    return None


def lines(events: list[dict], multi_day: bool, now: datetime | None = None) -> list[str]:
    """One line per event, the way the model should say it: '14:00-15:00 Physics class (Meet link)'.
    With `now` (Singapore time), each timed event also says where it is against the clock: "(done, ended 1h ago)",
    "(on now, ends 16:30)", "(in 3h)", so a small model never has to compare clock times to know what is still ahead."""
    res = []
    for e in events[:MAX_EVENTS]:
        day = f"{e['start']:%a %d %b} " if multi_day else ""
        t = "all day" if e["all_day"] else (f"{e['start']:%H:%M}-{e['end']:%H:%M}" if e["end"] else f"{e['start']:%H:%M}")
        extra = []
        if e["location"]:
            extra.append(e["location"])
        if e["link"]:
            extra.append("has a video link")
        if now is not None and not e["all_day"] and (rel := _relative(e, now.astimezone(SGT))):
            extra.append(rel)
        res.append(f"{day}{t} {e['title']}" + (f" ({', '.join(extra)})" if extra else ""))
    return res


SUBJECTS = (("Chem", ("chem",)), ("Math", ("math", "calculus", "algebra")), ("Physics", ("physic",)),
            ("Econ", ("econ",)), ("English", ("english", "lang lit", "literature")), ("Spanish", ("spanish", "español")))


def guess_subject(title: str) -> str | None:
    t = title.lower()                       # a word's start only: "econ" is inside "second" and "reconnect"
    return next((name for name, keys in SUBJECTS if any(re.search(r"\b" + re.escape(k), t) for k in keys)), None)


_CLASSY = re.compile(r"\b(class|lesson|lecture|tutor|tuition|seminar)\b", re.I)


def _cur(e: dict, now: datetime) -> dict:
    """The dict class.now and the class moves pass around for one event."""
    return {"title": e["title"], "online": bool(e["link"]), "link": e["link"], "subject": guess_subject(e["title"]),
            "cal_event_id": None, "starts_in": (e["start"] - now).total_seconds(),
            "key": f"{e['title']}@{e['start'].isoformat()}", "is_class": guess_subject(e["title"]) is not None or bool(_CLASSY.search(e["title"]))}


def current(events: list[dict], now: datetime, lead: int = 10) -> dict | None:
    """The class he is probably in: a timed event that starts up to `lead` minutes from now or is on and not over.
    `online` is true when it has a video link. When several match, a class beats anything else and the latest start
    wins: at 19:58 the 20:00 class matters more than the 19:00-20:00 homework block still running."""
    on = [e for e in events if not e["all_day"] and e["end"] is not None
          and e["start"] - timedelta(minutes=lead) <= now < e["end"]]
    if not on:
        return None
    def classy(e: dict) -> bool:
        return guess_subject(e["title"]) is not None or bool(_CLASSY.search(e["title"]))
    return _cur(max(on, key=lambda e: (classy(e), e["start"])), now)


# words every class event can carry: matching a fact on one of these would hand him another class's link
_GENERIC = {"class", "lesson", "session", "with", "online", "virtual", "zoom", "meet", "meeting", "tuition", "tutor",
            "tutorial", "lecture", "seminar", "weekly", "live", "period", "group", "review", "task", "reading",
            "homework", "block", "test", "exam", "revision", "study", "lab"}


def _names(title: str, subject: str | None) -> set[str]:
    """The words that say which class an event or a fact is about: its subject and the title's own words."""
    names = {w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in _GENERIC}
    if subject:
        names.add(subject.lower())
    return names


def link_from_facts(rows: list[dict], title: str, subject: str | None) -> str | None:
    """A meeting link Isaac told Misty ("save the link so it opens your English class") when the calendar event has none.
    A fact counts when its key, title or text names the class (its subject, or a word of the event title) and holds a
    meeting link in its link or its text."""
    names = _names(title, subject)
    for r in rows:
        blob = f"{r.get('key') or ''} {r.get('link_title') or ''} {r.get('text') or ''}".lower()
        if not any(n in blob for n in names):
            continue
        for field in ("link", "text"):
            if m := _MEET_URL.search(r.get(field) or ""):
                return m.group(0).rstrip(".,)")
    return None


_SAYS_ONLINE = re.compile(r"\b(online|zoom|google meet|meet\.|teams|zoho|webex|virtual)\b", re.I)


def says_online(rows: list[dict], title: str, subject: str | None) -> bool:
    """A fact about this class that says it is held online ("his online physics class is on Google Meet")."""
    names = _names(title, subject)
    for r in rows:
        blob = f"{r.get('key') or ''} {r.get('link_title') or ''} {r.get('text') or ''}".lower()
        if any(n in blob for n in names) and "class" in blob and _SAYS_ONLINE.search(blob):
            return True
    return False


def class_for(events: list[dict], now: datetime, title: str | None = None, lead: int = 15) -> dict | None:
    """The class to start when he says "start my class". No title (or one that names nothing, like "my class"): the class
    that is on or starts within `lead` minutes (a non-class event is not one). A title ("English class"): today's timed event
    that names the same class, the one on now first, else the nearest still to come; None when today has no such event."""
    want = _names(title or "", guess_subject(title or ""))
    if not want:
        cur = current(events, now, lead)
        return cur if cur is not None and cur["is_class"] else None
    mine = [e for e in events if not e["all_day"] and e["end"] is not None and e["start"].date() == now.date()
            and e["end"] > now and want & _names(e["title"], guess_subject(e["title"]))]
    if not mine:
        return None
    soon = [e for e in mine if e["start"] - timedelta(minutes=lead) <= now]
    return _cur(max(soon, key=lambda e: e["start"]) if soon else min(mine, key=lambda e: e["start"]), now)


def has_name(title: str) -> bool:
    """True when a title names a class (a subject or a word of its own), not just "my class"."""
    return bool(_names(title or "", guess_subject(title or "")))
