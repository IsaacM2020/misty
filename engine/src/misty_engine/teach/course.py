"""E14: a course by talking. "Get me ready for X by May" becomes a plan, day by day, that he can change by talking.

`layout` is pure: topics in need-first order in, days out. Nothing is ever scheduled before something it needs, a fifth of the days
stay free, reviews follow map.REVIEW_DAYS (two a day at most), and what does not fit is listed in `over` and said out loud. Nothing is
saved until he says yes: `propose` stores a proposal, `activate` is the yes. Nothing here knows a subject or a person.
"""
from __future__ import annotations

import calendar
import json
import math
import re
import secrets
from datetime import date, datetime, timedelta

from ..clock import SGT, sgt_date
from ..errors import Fatal
from . import build as tb
from . import map as tm
from . import plan as tplan

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
FREE_EVERY = 5                  # one day in five has nothing new, so a missed day is not a crisis
MAX_REVIEWS_PER_DAY = 2
MIN_PACE = 0.25
PLAN_AHEAD_DAYS = 3             # when a course starts, the first three days' topics get plans made in the background


def _eom(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _add_months(d0: date, n: int) -> date:
    m = d0.month - 1 + n
    y, m = d0.year + m // 12, m % 12 + 1
    return date(y, m, min(d0.day, calendar.monthrange(y, m)[1]))


def parse_by(text: str, today: str) -> str | None:
    """His date in words -> YYYY-MM-DD, or None. "by May" and "June" mean the end of that month; "15 June" that day; "in 3 weeks",
    "next month". A month already past means next year."""
    t = " ".join(str(text or "").lower().replace(",", " ").split())
    t = re.sub(r"^(by|before|until|to|on|at)\s+", "", t)
    t = re.sub(r"^(the )?end of\s+", "", t)
    if not t:
        return None
    now = date.fromisoformat(today)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        try:
            return date.fromisoformat(t).isoformat()
        except ValueError:
            return None
    m = re.fullmatch(r"in (\d+) (day|week|month)s?", t)
    if m:
        n = int(m.group(1))
        if m.group(2) == "month":
            return _add_months(now, n).isoformat()
        return (now + timedelta(days=n * (7 if m.group(2) == "week" else 1))).isoformat()
    if t == "next month":
        return _eom(*_ym(_add_months(now, 1))).isoformat()
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? ([a-z]+)", t) or re.fullmatch(r"([a-z]+) (\d{1,2})(?:st|nd|rd|th)?", t)
    if m:
        a, b = m.groups()
        day, mon = (int(a), MONTHS.get(b)) if a.isdigit() else (int(b), MONTHS.get(a))
        if mon and 1 <= day <= calendar.monthrange(now.year, mon)[1]:
            c = date(now.year, mon, day)
            return (c if c >= now else date(now.year + 1, mon, min(day, calendar.monthrange(now.year + 1, mon)[1]))).isoformat()
        return None
    if t in MONTHS:
        mon = MONTHS[t]
        c = _eom(now.year, mon)
        return (c if c >= now else _eom(now.year + 1, mon)).isoformat()
    return None


def _ym(d0: date) -> tuple[int, int]:
    return d0.year, d0.month


# ---- layout (pure) ------------------------------------------------------------------------------------------------------
def _free_days(n_days: int) -> set[int]:
    return {i for i in range(n_days) if (i + 1) % FREE_EVERY == 0}


def layout(topics: list[dict], done_levels: dict, today: str, by: str | None, per_day: float | None, *,
           skip: set | frozenset = frozenset(), reviews: list[int] | None = None) -> dict:
    """topics: [{topic_id, needs: [topic_id]}] with every topic after what it needs. done_levels: {topic_id: level}; solid or mastered are skipped.
    per_day: new topics a day (can be under 1); None means the pace that just fits. by None means no date: a horizon from the pace.
    Returns {days: [{day, new, review}], over: [topic_id], free: [dates], per_day, by, finish}."""
    offs = list(tm.REVIEW_DAYS if reviews is None else reviews)
    t0 = date.fromisoformat(today)
    todo = [t for t in topics if done_levels.get(t["topic_id"]) not in ("solid", "mastered") and t["topic_id"] not in skip]
    n = len(todo)
    pace = float(per_day) if per_day else None
    if by is None:
        p = pace or 1.0
        need = math.ceil(n / p) if n else 1
        total = need
        while total - len(_free_days(total)) < need:
            total += 1
        by = (t0 + timedelta(days=total - 1)).isoformat()
    n_days = max(0, (date.fromisoformat(by) - t0).days + 1)
    free = _free_days(n_days)
    work = [i for i in range(n_days) if i not in free]
    if pace is None:
        pace = max(1.0, math.ceil(n / len(work))) if work and n else 1.0
    day_of: dict[str, int] = {}
    new_on: dict[int, list[str]] = {}
    queue = list(todo)
    credit = 0.0
    for i in work:
        if not queue:
            break
        credit += pace
        k = int(credit + 1e-9)
        credit -= k
        slot = 0
        while slot < k and queue:
            t = queue[0]
            waiting = {q["topic_id"] for q in queue[1:]}
            if any(nd in waiting for nd in t["needs"]) or any(day_of.get(nd) == i for nd in t["needs"]):
                break                                   # a need not placed yet, or placed today: not before it, never the same day
            queue.pop(0)
            day_of[t["topic_id"]] = i
            new_on.setdefault(i, []).append(t["topic_id"])
            slot += 1
    over = [t["topic_id"] for t in queue]
    rev_on: dict[int, list[str]] = {}
    for tid, di in sorted(day_of.items(), key=lambda kv: (kv[1], kv[0])):
        for off in offs:
            for j in work:
                if j >= di + off and len(rev_on.get(j, [])) < MAX_REVIEWS_PER_DAY and tid not in rev_on.get(j, []):
                    rev_on.setdefault(j, []).append(tid)
                    break
    days = []
    for i in sorted(set(new_on) | set(rev_on)):
        days.append({"day": (t0 + timedelta(days=i)).isoformat(), "new": new_on.get(i, []), "review": rev_on.get(i, [])})
    finish = (t0 + timedelta(days=max(day_of.values()))).isoformat() if day_of else ""
    return {"days": days, "over": over, "free": [(t0 + timedelta(days=i)).isoformat() for i in sorted(free)],
            "per_day": pace, "by": by, "finish": finish, "n": n}


def summary_text(lay: dict, total: int, by: str) -> str:
    """What she says back: the plan in a few words, and what does not fit, plainly."""
    fits = lay["n"] - len(lay["over"])
    pace = lay["per_day"]
    p = f"{pace:g} a day" if pace >= 1 else f"one every {round(1 / pace)} days"
    if lay["over"]:
        return (f"This fits {fits} of {lay['n']} topics by {by}, at {p}: cut {len(lay['over'])} or move the date.")
    return f"{lay['n']} topics by {by}, {p}, finishing {lay['finish'] or 'today'}, a free day in every five, and reviews mixed in."


# ---- the stored course ------------------------------------------------------------------------------------------------
def _today(ctx) -> str:
    return sgt_date(ctx.clock.now())


def _settings(row) -> dict:
    try:
        return json.loads(row["proposed_json"] or "{}")
    except ValueError:
        return {}


def current(ctx, subject_id: str, statuses=("proposed", "active", "paused")):
    marks = ",".join("?" * len(statuses))
    return ctx.con.execute(f"SELECT * FROM teach_courses WHERE subject_id=? AND status IN ({marks}) ORDER BY "
                           f"CASE status WHEN 'proposed' THEN 0 ELSE 1 END, updated_at DESC LIMIT 1", (subject_id, *statuses)).fetchone()


def _topic_inputs(ctx, subject_id: str) -> list[dict]:
    ordered = tb.ordered(ctx, subject_id)
    key_to_id = {t["key"]: t["topic_id"] for t in ordered}
    return [{"topic_id": t["topic_id"], "needs": [key_to_id[k] for k in t["needs"] if k in key_to_id]} for t in ordered]


def _levels(ctx, topics: list[dict]) -> dict:
    now = ctx.clock.now()
    return {t["topic_id"]: tm.level(ctx, t["topic_id"], now) for t in topics}


def _done_topics(ctx, course_id: str) -> set:
    return {r[0] for r in ctx.con.execute("SELECT topic_id FROM teach_course_days WHERE course_id=? AND kind='new' AND status='done'", (course_id,))}


def _compute(ctx, subject_id: str, by: str | None, per_day, skip: set, course_id: str | None = None, today: str | None = None) -> dict:
    topics = _topic_inputs(ctx, subject_id)
    levels = _levels(ctx, topics)
    done = _done_topics(ctx, course_id) if course_id else set()
    return layout([t for t in topics if t["topic_id"] not in done], levels, today or _today(ctx), by, per_day, skip=skip | done)


def _titles(ctx, ids) -> list[str]:
    return [r[0] for i in ids for r in ctx.con.execute("SELECT title FROM teach_topics WHERE id=?", (i,))]


def _result(ctx, c, lay: dict, extra: dict | None = None) -> dict:
    total = lay["n"]
    return {"ok": True, "course": c["id"], "status": c["status"], "topics": total, "over": len(lay["over"]), "over_titles": _titles(ctx, lay["over"][:6]),
            "per_day": lay["per_day"], "by": lay["by"], "finish": lay["finish"], "summary": summary_text(lay, total, lay["by"]), **(extra or {})}


def propose(ctx, subject_id: str, goal: str, by_text: str, pace: float | None = None) -> dict:
    """Lay the course out and store it as a proposal. Saves no plan: only `activate` does."""
    today = _today(ctx)
    by = parse_by(by_text, today) if str(by_text or "").strip() else None
    if str(by_text or "").strip() and by is None:
        raise Fatal(f"I didn't catch the date \"{by_text}\": say it like 15 June, end of May or in 8 weeks")
    if by is None and not pace:
        raise Fatal("by when? give me a date, or how many topics a day")
    if not _topic_inputs(ctx, subject_id):
        raise Fatal("that subject has no topics yet: the map has to be built first")
    lay = _compute(ctx, subject_id, by, pace, set())
    now = ctx.clock.now()
    with ctx.con:
        ctx.con.execute("DELETE FROM teach_courses WHERE subject_id=? AND status='proposed'", (subject_id,))
        cid = "co_" + secrets.token_hex(5)
        ctx.con.execute("INSERT INTO teach_courses(id, subject_id, goal, by_date, per_day, status, proposed_json, created_at, updated_at) VALUES (?,?,?,?,?,'proposed',?,?,?)",
                        (cid, subject_id, " ".join(str(goal or "").split())[:200], lay["by"], lay["per_day"],
                         json.dumps({"skip": [], "manual_pace": bool(pace), "over": lay["over"]}), now, now))
    return _result(ctx, ctx.con.execute("SELECT * FROM teach_courses WHERE id=?", (cid,)).fetchone(), lay, {"proposed": True})


def _write_days(ctx, c, lay: dict) -> None:
    with ctx.con:
        ctx.con.execute("DELETE FROM teach_course_days WHERE course_id=? AND status='planned'", (c["id"],))
        for x in lay["days"]:
            for kind, ids in (("new", x["new"]), ("review", x["review"])):
                for t in ids:
                    ctx.con.execute("INSERT OR IGNORE INTO teach_course_days(course_id, day, topic_id, kind, status) VALUES (?,?,?,?,'planned')", (c["id"], x["day"], t, kind))


def _plan_ahead(ctx, subject_id: str, lay: dict) -> int:
    ids = [t for x in lay["days"][:PLAN_AHEAD_DAYS] for t in x["new"]]
    n = sum(tplan.queue_plan(ctx, t)["action"] == "queued" for t in ids)
    return n + tplan.queue_ready(ctx, subject_id, limit=3)["queued"]


def activate(ctx, subject_id: str) -> dict:
    """His yes: the proposal becomes the course. An older course of the subject is dropped (its days stay)."""
    c = current(ctx, subject_id, ("proposed",))
    if c is None:
        raise Fatal("there is no plan waiting for a yes: tell me the goal and the date first")
    now = ctx.clock.now()
    with ctx.con:
        ctx.con.execute("UPDATE teach_courses SET status='dropped', updated_at=? WHERE subject_id=? AND status IN ('active','paused')", (now, subject_id))
        ctx.con.execute("UPDATE teach_courses SET status='active', updated_at=? WHERE id=?", (now, c["id"]))
    c = ctx.con.execute("SELECT * FROM teach_courses WHERE id=?", (c["id"],)).fetchone()
    st = _settings(c)
    lay = _compute(ctx, subject_id, c["by_date"], c["per_day"] if st.get("manual_pace") else None, set(st.get("skip") or []), c["id"])
    _write_days(ctx, c, lay)
    queued = _plan_ahead(ctx, subject_id, lay)
    return _result(ctx, c, lay, {"plans_queued": queued})


def _relayout(ctx, c) -> dict:
    st = _settings(c)
    lay = _compute(ctx, c["subject_id"], c["by_date"], c["per_day"] if st.get("manual_pace") else None, set(st.get("skip") or []), c["id"])
    with ctx.con:
        ctx.con.execute("UPDATE teach_courses SET per_day=?, by_date=?, proposed_json=?, updated_at=? WHERE id=?",
                        (lay["per_day"], lay["by"], json.dumps({**st, "over": lay["over"]}), ctx.clock.now(), c["id"]))
    c = ctx.con.execute("SELECT * FROM teach_courses WHERE id=?", (c["id"],)).fetchone()
    if c["status"] in ("active", "paused"):
        _write_days(ctx, c, lay)
        _plan_ahead(ctx, c["subject_id"], lay)
    return _result(ctx, c, lay)


def change(ctx, subject_id: str, what: str, *, topic: str = "", pace: str = "", by: str = "") -> dict:
    """skip_topic {topic} | pace {pace: slower|faster|a number} | deadline {by}. Each re-lays out the days not done and says the result."""
    c = current(ctx, subject_id)
    if c is None:
        raise Fatal("he has no course for that yet: make one first")
    st = _settings(c)
    if what == "skip_topic":
        t = tb.find_topic(ctx, subject_id, topic)
        st["skip"] = sorted(set(st.get("skip") or []) | {t["id"]})
    elif what == "pace":
        w = str(pace or "").strip().lower()
        cur = float(c["per_day"] or 1.0)
        if w in ("slower", "slow", "slow down", "less", "half"):
            new = max(MIN_PACE, cur / 2)
        elif w in ("faster", "fast", "speed up", "more", "double"):
            new = cur * 2
        else:
            try:
                new = max(MIN_PACE, float(w))
            except ValueError:
                raise Fatal("slower, faster, or a number of topics a day") from None
        st["manual_pace"] = True
        with ctx.con:
            ctx.con.execute("UPDATE teach_courses SET per_day=? WHERE id=?", (new, c["id"]))
    elif what == "deadline":
        nb = parse_by(by, _today(ctx))
        if nb is None:
            raise Fatal(f"I didn't catch the date \"{by}\": say it like 15 June, end of May or in 8 weeks")
        with ctx.con:
            ctx.con.execute("UPDATE teach_courses SET by_date=? WHERE id=?", (nb, c["id"]))
    else:
        raise Fatal("change is skip_topic, pace or deadline for a course")
    with ctx.con:
        ctx.con.execute("UPDATE teach_courses SET proposed_json=? WHERE id=?", (json.dumps(st), c["id"]))
    c = ctx.con.execute("SELECT * FROM teach_courses WHERE id=?", (c["id"],)).fetchone()
    return _relayout(ctx, c)


def set_status(ctx, subject_id: str, status: str) -> dict:
    """pause keeps the plan and stops the daily bite; drop ends it (the days stay for the record)."""
    c = current(ctx, subject_id)
    if c is None:
        raise Fatal("he has no course for that")
    with ctx.con:
        ctx.con.execute("UPDATE teach_courses SET status=?, updated_at=? WHERE id=?", (status, ctx.clock.now(), c["id"]))
    return {"ok": True, "course": c["id"], "status": status}


def info(ctx, subject_id: str) -> dict:
    c = current(ctx, subject_id)
    if c is None:
        return {}
    st = _settings(c)
    return {"course": {"status": c["status"], "goal": c["goal"], "by": c["by_date"], "per_day": c["per_day"]}, "skipped": _titles(ctx, st.get("skip") or [])}


# ---- E15: the days as they go (done, missed), and a plan that bends -------------------------------------------------------
MISS_IN_A_ROW = 3
MISS_IN_SEVEN = 4


def _midnight(day: str) -> float:
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, tzinfo=SGT).timestamp()


def _did(ctx, c, r) -> bool:
    """A new topic is done when he had a lesson or a drill on it after the course began (early or late). A review is done when he had a
    drill on it that day or after."""
    if r["kind"] == "new":
        since, origins = c["created_at"], ("lesson", "drill")
    else:
        since, origins = _midnight(r["day"]), ("drill",)
    marks = ",".join("?" * len(origins))
    return ctx.con.execute(f"SELECT 1 FROM teach_evidence WHERE topic_id=? AND at>=? AND origin IN ({marks}) LIMIT 1", (r["topic_id"], since, *origins)).fetchone() is not None


def sync(ctx, course_id: str, today: str) -> dict:
    """Mark the days: done when he did it (a topic he finishes early is done before its day), missed when its day went by without it.
    A missed topic stays missed (it is offered again) until it is done or a re-plan moves it."""
    c = ctx.con.execute("SELECT * FROM teach_courses WHERE id=?", (course_id,)).fetchone()
    if c is None:
        return {"done": 0, "missed": 0}
    done = missed = 0
    for r in ctx.con.execute("SELECT * FROM teach_course_days WHERE course_id=? AND status IN ('planned','missed')", (course_id,)).fetchall():
        if r["kind"] == "review" and r["day"] > today:
            continue
        if _did(ctx, c, r):
            new = "done"
        elif r["status"] == "planned" and r["day"] < today:
            new = "missed"
        else:
            continue
        with ctx.con:
            ctx.con.execute("UPDATE teach_course_days SET status=? WHERE course_id=? AND day=? AND topic_id=? AND kind=?",
                            (new, course_id, r["day"], r["topic_id"], r["kind"]))
        done += new == "done"
        missed += new == "missed"
    return {"done": done, "missed": missed}


def today_items(ctx, course_id: str, today: str) -> dict:
    """What is still to do today: missed new topics from earlier (oldest first), then today's new topics, and today's reviews."""
    new = [r[0] for r in ctx.con.execute("SELECT topic_id FROM teach_course_days WHERE course_id=? AND kind='new' AND "
                                         "((status='missed' AND day<?) OR (status='planned' AND day=?)) ORDER BY (status='planned'), day, topic_id", (course_id, today, today))]
    rev = [r[0] for r in ctx.con.execute("SELECT topic_id FROM teach_course_days WHERE course_id=? AND kind='review' AND status='planned' AND day=? ORDER BY topic_id", (course_id, today))]
    return {"new": new, "review": rev}


def missed_trigger(ctx, course_id: str, today: str) -> bool:
    """Three missed days in a row, or four of the last seven. Only days that had a new topic count; a re-planned day (moved) is not missed."""
    by_day: dict[str, str] = {}
    for r in ctx.con.execute("SELECT day, status FROM teach_course_days WHERE course_id=? AND kind='new' AND day<? AND status IN ('done','missed','moved')", (course_id, today)):
        cur = by_day.get(r["day"])
        by_day[r["day"]] = "missed" if "missed" in (cur, r["status"]) else ("done" if "done" in (cur, r["status"]) else r["status"])
    days = sorted(by_day, reverse=True)
    if len(days) >= MISS_IN_A_ROW and all(by_day[d] == "missed" for d in days[:MISS_IN_A_ROW]):
        return True
    since = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    return sum(1 for d in days if d >= since and by_day[d] == "missed") >= MISS_IN_SEVEN


def replan(ctx, course_id: str, today: str | None = None) -> dict:
    """In plain code: the new topics that are left are spread evenly over the days that are left, in the same order, every topic after what it
    needs, no review past the deadline. What no longer fits is listed (`over`, by title) and never dropped without saying. The missed
    days are marked moved, so the same misses do not start it again."""
    c = ctx.con.execute("SELECT * FROM teach_courses WHERE id=?", (course_id,)).fetchone()
    if c is None:
        raise Fatal("he has no course for that")
    today = today or _today(ctx)
    st = _settings(c)
    lay = _compute(ctx, c["subject_id"], c["by_date"], None, set(st.get("skip") or []), c["id"], today)
    with ctx.con:
        n = ctx.con.execute("UPDATE teach_course_days SET status='moved' WHERE course_id=? AND kind='new' AND status='missed'", (course_id,)).rowcount
        st["manual_pace"] = False
        st["over"] = lay["over"]
        ctx.con.execute("UPDATE teach_courses SET per_day=?, proposed_json=?, updated_at=? WHERE id=?", (lay["per_day"], json.dumps(st), ctx.clock.now(), course_id))
    c = ctx.con.execute("SELECT * FROM teach_courses WHERE id=?", (course_id,)).fetchone()
    _write_days(ctx, c, lay)
    _plan_ahead(ctx, c["subject_id"], lay)
    return {"ok": True, "course": course_id, "over": _titles(ctx, lay["over"]), "cut": len(lay["over"]), "moved": n,
            "per_day": lay["per_day"], "by": lay["by"], "finish": lay["finish"]}
