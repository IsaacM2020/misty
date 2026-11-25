"""Class moves: "start my class" by voice or chat does what the reach-out card's Yes does, in one move, and a class link he
tells her is kept for next time.

class_start     the class on now or starting within 15 minutes (or the one he names): open its join link, if there is one, and
                start the recording. The engine cannot hear, so recording is the APP's job: the engine asks it over the hands
                wire (op `class_start`), and the app starts the class exactly as the ask card does (ClassVM.start). The calendar
                event is marked handled, so the card never asks about the same class after.
class_link_save keeps "the English class link is <url>" as a fact keyed by the class ("english class link"). It is the same
                place the calendar-less link already came from (class.now reads facts), so class.now and class_start both find it.
"""
from __future__ import annotations

import re
from datetime import datetime

from .. import calendar_now, class_reach, facts, gate
from ..clock import SGT
from ..errors import Fatal, MistyError
from .base import Move, Outcome, Param
from .registry import register

LEAD_MIN = 15                 # "start my class" reaches this far ahead of the start
_SKIP = {"class", "lesson", "link", "the", "my", "online", "zoom", "meet", "session", "for", "to", "of", "a"}


def _name(title: str) -> str:
    return " ".join(title.split()).strip(" .")[:60]


async def _calendar(ctx) -> list[dict] | None:
    """Today's events: the shared cache, else one read of Google. None when the calendar cannot be reached."""
    from .. import now as nowlib
    evs = nowlib.cached(ctx, nowlib.FRESH_S)
    if evs is None and ctx.net.online and await nowlib.refresh(ctx):
        evs = nowlib.cached(ctx, 60)
    return evs


def _record_subject(ctx, cur: dict) -> str:
    if cur.get("subject"):
        return cur["subject"]
    prev = class_reach.earlier_session(ctx.con, cur["title"])
    return (prev["subject"] if prev is not None else None) or "Other"


async def _record(ctx, cur: dict) -> tuple[str, str]:
    """(status, why): started | queued | already, or failed with a plain reason. `queued` is a voice turn: the app waits for
    her reply to finish before it takes the mic for the class (starting the class would cut the reply off)."""
    if gate.in_session(ctx.con):
        return "already", ""
    hands = (ctx.extras or {}).get("hands")
    if hands is None or not getattr(hands, "connected", False):
        return "failed", "the app isn't connected"
    try:
        d = await hands._call("class_start", title=cur["title"], subject=_record_subject(ctx, cur), online=bool(cur.get("online")),
                              key=cur.get("key") or "")
    except (Fatal, MistyError) as e:
        return "failed", f"the app didn't answer ({e})"
    if d.get("ok") is False:
        return "failed", str(d.get("problem") or "the app couldn't start it")
    return str(d.get("status") or "started"), ""


async def _class_start(ctx, p) -> Outcome:
    from .. import chat
    asked = (p.get("title") or "").strip()
    evs = await _calendar(ctx)
    now = datetime.fromtimestamp(ctx.clock.now(), SGT)
    cur = calendar_now.class_for(evs or [], now, asked, LEAD_MIN)
    if cur is None:
        if asked and calendar_now.has_name(asked):         # he named it and the calendar does not have it: still do what he asked
            cur = {"title": asked, "online": False, "link": None, "subject": calendar_now.guess_subject(asked),
                   "cal_event_id": None, "starts_in": 0.0, "key": None, "is_class": True}
        elif evs is None:
            return Outcome(False, what="she can't see his calendar right now, so she doesn't know which class to start. Ask which class")
        else:
            return Outcome(False, what=f"there's no class on right now or starting in the next {LEAD_MIN} minutes")
    class_reach.with_saved_link(ctx, cur)
    status, why = await _record(ctx, cur)
    opened, link_why = False, ""
    if cur.get("link") and re.fullmatch(r"https://\S{4,1000}", cur["link"]):
        try:
            await chat._open(cur["link"])
            opened = True
        except Exception:                                  # `open` failed: the class may already be recording, so say both
            link_why = "couldn't open its link"
    if status != "failed" and cur.get("key"):
        class_reach.mark_handled(ctx, cur["key"])          # the reach-out card must not ask about this class again
    t = _name(cur["title"])
    data = {"title": t, "recording": status, "link_opened": opened, "has_link": bool(cur.get("link"))}
    if status == "failed":
        said = f"couldn't start recording {t}: {why}"
        if opened:
            said += ". Its link is open"
        return Outcome(False, what=said, data=data)
    bits = [("Already recording " if status == "already" else "Recording ") + t + "."]
    if opened:
        bits.append("Opened its link.")
    elif link_why:
        bits.append(link_why[0].upper() + link_why[1:] + ".")
    elif not cur.get("link"):
        bits.append("No join link on the calendar or saved for it.")
    return Outcome(True, what=" ".join(bits), data=data)


async def _recording(ctx, p, out) -> bool:
    if out.data.get("recording") == "queued":
        return True                    # the app took it and starts once her reply is spoken: nothing to read back yet
    return gate.in_session(ctx.con)


def _link_key(title: str) -> str:
    name = " ".join(w for w in re.sub(r"[^\w' &-]+", " ", title).split() if w.lower() not in _SKIP)
    return f"{name.lower()} class link" if name else ""


async def _link_save(ctx, p) -> Outcome:
    url, title = p["url"], p["title"]
    m = calendar_now._MEET_URL.search(url)
    if not m or m.start() != 0:
        return Outcome(False, what="that isn't a Zoom, Google Meet, Teams, Webex or similar meeting link")
    key = _link_key(title)
    if not key:
        return Outcome(False, what="which class is it for?")
    name = key[: -len(" class link")]
    try:
        facts.remember(ctx, key, f"{name.title()} class link: {url}", link=url, link_title=f"{name.title()} class", exact=True)
    except Fatal as e:
        return Outcome(False, what="that link looks like it holds a password, so she won't keep it" if str(e) == "LOOKS_SECRET" else f"couldn't keep it ({e})")
    return Outcome(True, what=f"saved the {name.title()} class link", data={"key": key, "url": url, "name": name})


async def _link_saved(ctx, p, out) -> bool:
    rows = [dict(r) for r in ctx.con.execute("SELECT key, text, link, link_title FROM facts WHERE status='active'")]
    return calendar_now.link_from_facts(rows, out.data["name"] + " class", calendar_now.guess_subject(out.data["name"])) == p["url"]


register(Move("class_start", "start his class now: opens its join link (from the calendar, else the one he saved) AND starts the class "
              "recording, in one go, like accepting the reach-out card. title is optional: leave it out for the class that is on now "
              "or starts within 15 minutes. Call it straight away when he says start, join, record or open his class: never tell him to "
              "join it himself and never say it is \"already on\" or \"running\" from what you can see. If there is no link, say that in "
              "a few words; when he then gives one, call class_link_save, then class_start again (it won't record twice, it just opens the link)",
              {"title": Param(str, required=False, max_len=120, doc="the class, like 'English class'; leave out for the one on now")},
              "safe", _class_start, _recording, no_text=True,
              says=("start my English class", "can you start my class please", "join my physics class and record it", "open my class link and start recording"),
              not_mine=(("open my class notes from Tuesday", "class_notes"), ("what time is my next class", "calendar"))))
register(Move("class_link_save", "keep the meeting link for one of his classes so class_start can open it: title is the class "
              "('English class'), url its Zoom, Meet or Teams link. Call it when he tells you or pastes a class link",
              {"title": Param(str, max_len=120, doc="the class, like 'English class'"), "url": Param(str, max_len=500, doc="its https meeting link")},
              "safe", _link_save, _link_saved, no_text=True,
              says=("the English class link is https://zoom.us/j/123456789", "save this link for my physics class https://meet.google.com/abc-defg-hij",
                    "my chem class link is https://zoom.us/j/55501234"),
              not_mine=(("open https://zoom.us/j/123456789 for me", "move"), ("what's on my calendar tomorrow", "calendar"))))
