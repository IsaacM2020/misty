"""Edith reaches out first about class. Plain code, no model.

Before a class: `suggest` says auto (he has recorded this class before: just start it), ask (never recorded: ask once)
or none. The app acts on it; `reach` marks the event handled (never twice) and says the line through the gate.
After a class: when the notes are done, `after_class_line` turns the undone actions into one short line."""
from __future__ import annotations

import re
from datetime import date, datetime

from . import calendar_now, classes, gate
from .clock import SGT
from .logs import log_event

LEAD_S = 120            # from 2 minutes before the start...
LATE_S = -600           # ...until 10 minutes after it
KEEP = 60               # event keys remembered
_KIND_RANK = {"test": 0, "homework": 1, "reading": 2, "bring": 3, "other": 4}
MAX_TEXT = 90


def _prefix(cal_title: str) -> str:
    return " ".join(cal_title.split())[:60].rstrip(" ,.")


def earlier_session(con, cal_title: str, cal_event_id: str | None = None):
    """A class recorded before for this calendar title ("Prashant Class, 14 Nov") or the same event series, else None."""
    if cal_event_id:
        r = con.execute("SELECT * FROM class_sessions WHERE cal_event_id=? AND merged_into IS NULL ORDER BY started_at DESC LIMIT 1",
                        (cal_event_id,)).fetchone()
        if r:
            return r
    p = _prefix(cal_title)
    if not p:
        return None
    for r in con.execute("SELECT * FROM class_sessions WHERE lower(title) LIKE ? ESCAPE '\\' AND merged_into IS NULL "
                         "ORDER BY started_at DESC", (p.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ", %",)):
        if re.fullmatch(r"\d{1,2} [A-Za-z]{3}", r["title"][len(p) + 2:]):
            return r
    return None


def with_saved_link(ctx, cur: dict) -> dict:
    """The invite has no link: maybe he saved one for this class (a fact), or told her it is held online. Fills `link` and
    `online` on the class dict in place and returns it."""
    if not cur.get("link"):
        facts = [dict(r) for r in ctx.con.execute("SELECT key, text, link, link_title FROM facts WHERE status='active'")]
        if link := calendar_now.link_from_facts(facts, cur["title"], cur.get("subject")):
            cur["link"], cur["online"] = link, True
        elif calendar_now.says_online(facts, cur["title"], cur.get("subject")):
            cur["online"] = True
    return cur


def handled(ctx) -> list[str]:
    return list(ctx.settings.get("class_reach").get("handled", []))


def mark_handled(ctx, key: str) -> None:
    h = [k for k in handled(ctx) if k != key] + [key]
    ctx.settings.set("class_reach", {"handled": h[-KEEP:]})


def decide(cur: dict | None, *, in_class: bool, was_recorded: bool, done: bool, quiet: bool) -> str:
    """auto | ask | none for the class the calendar says is starting."""
    if cur is None or not cur.get("is_class") or in_class or done or quiet:
        return "none"
    t = cur.get("starts_in")
    if t is None or not (LATE_S < t <= LEAD_S):
        return "none"
    return "auto" if was_recorded else "ask"


def suggest(ctx, cur: dict | None) -> dict:
    """What class.now adds to the calendar's answer: {suggest, subject?}. A class recorded before brings its subject."""
    if cur is None:
        return {"suggest": "none"}
    o = gate._opts(ctx)
    now = ctx.clock.now()
    prev = earlier_session(ctx.con, cur["title"]) if cur.get("is_class") else None
    out = decide(cur, in_class=gate.in_session(ctx.con), was_recorded=prev is not None, done=cur["key"] in handled(ctx),
                 quiet=gate.is_quiet(datetime.fromtimestamp(now, SGT).hour, o["quiet_from"], o["quiet_to"]))
    res = {"suggest": out}
    if out == "auto" and not cur.get("subject") and prev is not None:
        res["subject"] = prev["subject"]
    return res


def start_line(title: str, how: str) -> str:
    t = " ".join(title.split()).strip(" .")[:60]
    return f"Recording {t}." if how == "auto" else f"{t} is starting. Record it?"


async def reach(ctx, key: str, title: str, how: str) -> str:
    """The app is about to act on a suggestion: remember the event (never twice) and say the line if the gate allows.
    A line the gate holds is dropped, not kept: it would be stale (and blocked) once the class is running."""
    if how not in ("auto", "ask") or key in handled(ctx):
        return "skipped"
    mark_handled(ctx, key)
    m = gate.Moment(start_line(title, how), "now", topic="class", topic_text=title, key=f"class_start:{key}", plain=True, quiet=True)
    d = await gate.submit(ctx, m, hold=False)
    return d.action


# ---- after class

def _due(due: str | None, today: date) -> str:
    if not due:
        return ""
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return ""
    n = (d - today).days
    if n < 0:
        return " (was due " + d.strftime("%a") + ")" if n > -7 else " (overdue)"
    if n == 0:
        return " (due today)"
    if n == 1:
        return " (due tomorrow)"
    if n < 7:
        return f" (due {d:%a})"
    return f" (due {d.day} {d:%b})"


def _text(t: str) -> str:
    t = " ".join(t.split()).rstrip(" .")
    return (t[:MAX_TEXT].rstrip() + "...") if len(t) > MAX_TEXT else t


def after_class_line(subject: str, actions: list[dict], today: date) -> str | None:
    """"From Physics: Finish worksheet 4 (due Thu), and revise the kinematics test (due Mon)." Undone actions only; tests first,
    then the nearest due. Two at most, and "N more in your notes." when there are further ones. None when nothing is left to do."""
    todo = [a for a in actions if not a.get("done")]
    if not todo:
        return None
    todo.sort(key=lambda a: (_KIND_RANK.get(a.get("kind"), 4), a.get("due") or "9999-99-99"))
    top = [_text(a["text"]) + _due(a.get("due"), today) for a in todo[:2]]
    line = f"From {subject}: " + ", and ".join(top) + "."
    if len(todo) > 2:
        line += f" {len(todo) - 2} more in your notes."
    return line


async def after_class(ctx, sid: str) -> str | None:
    """Notes just finished: one line about what is still to do, through the gate (spoken if he is free). Once per class."""
    try:
        sess = classes.get(ctx.con, sid)
        line = after_class_line(sess["session"]["subject"], sess["actions"], datetime.fromtimestamp(ctx.clock.now(), SGT).date())
        if line is None:
            return None
        m = gate.Moment(line, "soon", topic="class", topic_text=f"your {sess['session']['subject']} class", key=f"after_class:{sid}", plain=True)
        return (await gate.submit(ctx, m)).action
    except Exception as e:                                   # a nicety: it never breaks the notes
        log_event("CLASS_REACH_FAIL", item_id=sid, code=type(e).__name__)
        return None
