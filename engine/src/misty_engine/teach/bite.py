"""E15: the daily bite. One gentle ask a day per active course, through the gate, and a plan that bends when he misses days.

A scheduler tick (every minute) does, for each active course:
  sync the days (done, missed), re-plan when three days in a row (or four of seven) were missed, plan tomorrow's topic in the evening,
  and offer today's bite once: "Ten minutes on <topic> for <course>?" (a lesson), "A quick drill on ..." on a review day.
The ask goes through the gate as an ordinary `soon` line (never `own`), so class, calls, quiet hours and the daily limit all apply. His yes
is `teach start` (or `teach drill`) on that topic, "later" keeps it planned and asks once more in the evening, "not today" marks it missed.
Scene 9h: when the last class's next step points at a topic he is shaky on or has not met, that topic jumps to today and the ask says why.
Nothing here knows a subject or a person.
"""
from __future__ import annotations

import json

from .. import gate
from ..clock import SGT, sgt_date
from ..logs import log_event
from ..openrouter import user_approved
from . import course as tc
from . import map as tm
from . import match
from . import plan as tplan

ANSWER_S = 2 * 3600           # an unanswered bite is forgotten after this
EVENING_H = 18                # "later" is asked once more from this hour; tomorrow's plan is queued from this hour
CLASS_DAYS = 3                # a class counts for this many days after it ended
JUMP_LEVELS = ("unknown", "seen", "shaky")
ANSWERS = ("later", "not_today")


# ---- the table ------------------------------------------------------------------------------------------------------
def _has(ctx, key: str) -> bool:
    return ctx.con.execute("SELECT 1 FROM teach_bite WHERE key=?", (key,)).fetchone() is not None


def _put(ctx, key: str, course_id="", day="", topic_id="", kind="", outcome="") -> None:
    with ctx.con:
        ctx.con.execute("INSERT OR REPLACE INTO teach_bite(key, at, course_id, day, topic_id, kind, outcome) VALUES (?,?,?,?,?,?,?)",
                        (key, ctx.clock.now(), course_id, day, topic_id, kind, outcome))


def _open(ctx):
    """The bite he has not answered yet (told in the last two hours)."""
    return ctx.con.execute("SELECT * FROM teach_bite WHERE outcome='told' AND at>=? ORDER BY at DESC LIMIT 1", (ctx.clock.now() - ANSWER_S,)).fetchone()


def _title(ctx, topic_id: str) -> str:
    r = ctx.con.execute("SELECT title FROM teach_topics WHERE id=?", (topic_id,)).fetchone()
    return r[0] if r else ""


def _course_title(ctx, subject_id: str) -> str:
    r = ctx.con.execute("SELECT title FROM teach_subjects WHERE id=?", (subject_id,)).fetchone()
    return r[0] if r else ""


def _active(ctx) -> list:
    return ctx.con.execute("SELECT * FROM teach_courses WHERE status='active' ORDER BY created_at, id").fetchall()


# ---- what to offer ---------------------------------------------------------------------------------------------------
def _today_pick(ctx, c, today: str) -> dict | None:
    it = tc.today_items(ctx, c["id"], today)
    if it["new"]:
        return {"topic_id": it["new"][0], "kind": "lesson", "why": ""}
    if it["review"]:
        return {"topic_id": it["review"][0], "kind": "drill", "why": ""}
    return None


def today_facts(ctx) -> list[dict]:
    """For the brief and work_on: what each active course has for today."""
    out = []
    today = sgt_date(ctx.clock.now())
    for c in _active(ctx):
        tc.sync(ctx, c["id"], today)
        p = _today_pick(ctx, c, today)
        if p:
            out.append({"course": _course_title(ctx, c["subject_id"]), "topic": _title(ctx, p["topic_id"]), "do": p["kind"]})
    return out


def _class_session(ctx, class_subject: str, now: float):
    return ctx.con.execute("SELECT * FROM class_sessions WHERE lower(subject)=lower(?) AND status='noted' AND merged_into IS NULL AND ended_at>=? "
                           "ORDER BY ended_at DESC LIMIT 1", (class_subject, now - CLASS_DAYS * 86400)).fetchone()


def _needs_known(ctx, topic_id: str, now: float) -> bool:
    for r in ctx.con.execute("SELECT needs_id FROM teach_needs WHERE topic_id=?", (topic_id,)).fetchall():
        if tm.level(ctx, r["needs_id"], now) in ("unknown", "seen"):
            return False
    return True


async def _class_jump(ctx, c, today: str) -> dict | None:
    """Scene 9h. The last class's next step, matched to a topic once; it jumps to today when he is shaky on it or has not met it, and not
    before something it needs. Said once per topic per class."""
    now = ctx.clock.now()
    sub = ctx.con.execute("SELECT * FROM teach_subjects WHERE id=?", (c["subject_id"],)).fetchone()
    if sub is None or not sub["class_subject"]:
        return None
    s = _class_session(ctx, sub["class_subject"], now)
    if s is None:
        return None
    try:
        text = ((json.loads(s["notes_json"] or "{}") or {}).get("next_step") or {}).get("text") or ""
    except ValueError:
        text = ""
    if not text.strip():
        return None
    mk = f"classmatch:{s['id']}"
    row = ctx.con.execute("SELECT * FROM teach_bite WHERE key=?", (mk,)).fetchone()
    if row is None:
        if not ctx.paid_allowed():                     # a small model call: only while paid calls are on, and tried again later
            return None
        with user_approved():
            tid = await match.topic_for(ctx, c["subject_id"], text)
        _put(ctx, mk, c["id"], today, tid or "", "match", tid or "none")
        topic_id = tid
    else:
        topic_id = row["topic_id"] or None
    if not topic_id:
        return None
    ck = f"class:{s['id']}:{topic_id}"
    lv = tm.level(ctx, topic_id, now)
    if _has(ctx, ck) or lv not in JUMP_LEVELS or not _needs_known(ctx, topic_id, now):
        return None
    with ctx.con:                                       # it jumps the queue: its own row moves to today
        ctx.con.execute("DELETE FROM teach_course_days WHERE course_id=? AND topic_id=? AND kind='new' AND status='planned'", (c["id"], topic_id))
        ctx.con.execute("INSERT OR REPLACE INTO teach_course_days(course_id, day, topic_id, kind, status) VALUES (?,?,?,'new','planned')", (c["id"], today, topic_id))
    word = "shaky" if lv == "shaky" else "new to it"
    return {"topic_id": topic_id, "kind": "lesson", "why": f"Your {s['title']} class pointed at {_title(ctx, topic_id)}, and you're {word} on it. Ten minutes now?", "class_key": ck, "class_id": s["id"]}


def _text(ctx, c, pick: dict, evening: bool) -> str:
    title, course = _title(ctx, pick["topic_id"]), _course_title(ctx, c["subject_id"])
    if pick.get("why"):
        return pick["why"]
    if evening:
        return f"Still want ten minutes on {title} for {course}?" if pick["kind"] == "lesson" else f"Still want a quick drill on {title} for {course}?"
    return f"Ten minutes on {title} for {course}?" if pick["kind"] == "lesson" else f"A quick drill on {title} for {course}?"


async def _offer(ctx, c, today: str, hour: int) -> dict | None:
    day_key = f"bite:{c['id']}:{today}"
    eve_key = day_key + ":eve"
    first = ctx.con.execute("SELECT * FROM teach_bite WHERE key=?", (day_key,)).fetchone()
    evening = False
    if first is not None:
        if first["outcome"] != "later" or hour < EVENING_H or _has(ctx, eve_key):
            return None
        evening, key = True, eve_key
        pick = _today_pick(ctx, c, today)
        if pick is None or pick["topic_id"] != first["topic_id"]:
            return None
    else:
        key = day_key
        pick = await _class_jump(ctx, c, today) or _today_pick(ctx, c, today)
        if pick is None:
            return None
    m = gate.Moment(_text(ctx, c, pick, evening), "soon", topic="teach", topic_text=f"your {_course_title(ctx, c['subject_id'])} course", key=key, plain=True)
    now = ctx.clock.now()
    d = gate._decide(ctx, m, now)
    if d.action == "wait":
        return None                                    # not now: tried again next minute
    if d.action == "drop":
        _put(ctx, key, c["id"], today, pick["topic_id"], "eve" if evening else pick["kind"], "dropped")
        return None
    if d.action == "ask":                              # our line is already a question: never the gate's "I've got something on..."
        await gate._deliver(ctx, m, "say", d.why, now)
    else:
        await gate.submit(ctx, m)
    _put(ctx, key, c["id"], today, pick["topic_id"], "eve" if evening else pick["kind"], "told")
    if pick.get("class_key"):
        _put(ctx, pick["class_key"], c["id"], today, pick["topic_id"], "class", "said")
    log_event("TEACH_BITE", code="told")
    return {"course": c["id"], "topic_id": pick["topic_id"], "kind": pick["kind"], "text": m.text, "evening": evening}


# ---- re-planning ------------------------------------------------------------------------------------------------------
async def _replan_if_needed(ctx, c, today: str) -> None:
    if not tc.missed_trigger(ctx, c["id"], today):
        return
    r = tc.replan(ctx, c["id"], today)
    log_event("TEACH_BITE", code="replan")
    if r["cut"]:
        names = ", ".join(r["over"][:6]) + (" and more" if len(r["over"]) > 6 else "")
        n = r["cut"]
        text = (f"I moved things around: {n} topic{'s' if n != 1 else ''} no longer fit{'s' if n == 1 else ''} before {r['by']}, "
                f"{'it is' if n == 1 else 'they are'} {names}. Cut them or move the date?")
        key = f"replan:{c['id']}:{today}"
        m = gate.Moment(text, "soon", topic="teach", topic_text=f"your {_course_title(ctx, c['subject_id'])} plan", key=key, plain=True)
        await gate.submit(ctx, m)
        _put(ctx, key, c["id"], today, "", "replan", "told-cut")
    else:
        await gate.silent_note(ctx, "I spread the rest of your plan over the days left, nothing was cut.", {"teach": "replan", "course": c["id"]})


def _plan_tomorrow(ctx, c, today: str) -> None:
    """The evening before, tomorrow's new topics get their plans (made in the background)."""
    from datetime import date, timedelta
    tomorrow = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    for r in ctx.con.execute("SELECT topic_id FROM teach_course_days WHERE course_id=? AND day=? AND kind='new' AND status='planned'", (c["id"], tomorrow)).fetchall():
        tplan.queue_plan(ctx, r["topic_id"])


# ---- the tick ---------------------------------------------------------------------------------------------------------
async def tick(ctx) -> list[dict]:
    """Called every minute. Returns what was offered."""
    from datetime import datetime
    from .. import chat
    now = ctx.clock.now()
    today = sgt_date(now)
    hour = datetime.fromtimestamp(now, SGT).hour
    out = []
    for c in _active(ctx):
        tc.sync(ctx, c["id"], today)
        await _replan_if_needed(ctx, c, today)
        if hour >= EVENING_H:
            _plan_tomorrow(ctx, c, today)
        if chat.teach_pending(ctx) is not None:        # a lesson or a drill is running: no ask
            continue
        o = await _offer(ctx, c, today, hour)
        if o:
            out.append(o)
    return out


# ---- his answer -------------------------------------------------------------------------------------------------------
def block(ctx) -> str:
    """Tells her what she offered, while his answer is still waiting, and what each answer is."""
    r = _open(ctx)
    if r is None:
        return ""
    title = _title(ctx, r["topic_id"])
    sub = ctx.con.execute("SELECT s.title FROM teach_courses c JOIN teach_subjects s ON s.id=c.subject_id WHERE c.id=?", (r["course_id"],)).fetchone()
    what = ("call teach with action start and topic " + title) if r["kind"] != "drill" else ("call teach with action drill and topic " + title)
    return (f"You just offered him a short study bite on \"{title}\"" + (f" for {sub[0]}" if sub else "") + " and he has not answered. If his message is a yes "
            f"(\"sure\", \"ok\", \"let's go\"), {what}, then carry on as that tool says. If he says later, or that he will do it tonight, call teach with action bite "
            "and answer later; if he says not today, no, or skip it, call teach with action bite and answer not_today, and say only a short okay. "
            "If his message is about something else, ignore this.")


def accept(ctx) -> None:
    """He said yes (the lesson or drill started): the open bite is taken."""
    r = _open(ctx)
    if r is not None:
        with ctx.con:
            ctx.con.execute("UPDATE teach_bite SET outcome='accepted' WHERE key=?", (r["key"],))


def answer(ctx, ans: str) -> dict:
    if ans not in ANSWERS:
        return {"ok": False, "error": "answer is later or not_today"}
    r = _open(ctx)
    if r is None:
        return {"ok": False, "error": "there is no study bite waiting for an answer"}
    if ans == "later":
        with ctx.con:
            ctx.con.execute("UPDATE teach_bite SET outcome='later' WHERE key=?", (r["key"],))
        return {"ok": True, "answer": "later", "say": "Okay, I'll ask once more this evening."}
    with ctx.con:
        ctx.con.execute("UPDATE teach_bite SET outcome='skipped' WHERE key=?", (r["key"],))
        ctx.con.execute("UPDATE teach_course_days SET status='missed' WHERE course_id=? AND topic_id=? AND kind=? AND status='planned' AND day<=?",
                        (r["course_id"], r["topic_id"], "review" if r["kind"] == "drill" else "new", r["day"]))
    return {"ok": True, "answer": "not_today", "say": "Okay."}
