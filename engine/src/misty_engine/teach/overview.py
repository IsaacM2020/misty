"""E18: where he is, in one view. The Radar "Learning" section reads this and nothing else (`teach.overview` RPC).

Pure code over the tables: no model call, no write, no subject or person named. Every number is counted from rows, so a
hand-checked table on a fixture db equals what the Radar shows.

  courses   per active or paused course: subject, goal and date, days left, topics solid or better, shaky, due now, the next bite.
  shaky     the shaky topics with a plain reason (the same words `teach level` says).
  due       topics he should be asked about again today.
  probes    the last few "1 of 3 before, 3 of 3 now" lines.
  drills    the last 7 days of drills, reviews and quick checks.
  mistakes  counts from the mistakes book, and the one pattern line if there is one.
"""
from __future__ import annotations

from datetime import date

from ..clock import sgt_date
from . import course as tc
from . import map as tm
from . import probe, slips

DAY = 86400.0
SHAKY_MAX = 12
DUE_MAX = 12
PROBES_MAX = 5
SOLID = ("solid", "mastered")


def _skipped(c) -> set:
    return set(tc._settings(c).get("skip") or [])


def _next_bite(ctx, c, today: str) -> dict | None:
    """What is still to do today, else the next planned day. New topics come before reviews."""
    items = tc.today_items(ctx, c["id"], today)
    for kind in ("new", "review"):
        if items[kind]:
            t = ctx.con.execute("SELECT title FROM teach_topics WHERE id=?", (items[kind][0],)).fetchone()
            if t:
                return {"topic": t[0], "day": today, "kind": kind}
    r = ctx.con.execute("SELECT d.day, d.kind, t.title FROM teach_course_days d JOIN teach_topics t ON t.id=d.topic_id "
                        "WHERE d.course_id=? AND d.status='planned' AND d.day>? ORDER BY d.day, (d.kind='review'), d.topic_id LIMIT 1", (c["id"], today)).fetchone()
    return {"topic": r["title"], "day": r["day"], "kind": r["kind"]} if r else None


def _days_left(c, today: str) -> int | None:
    if not c["by_date"]:
        return None
    return (date.fromisoformat(c["by_date"]) - date.fromisoformat(today)).days


def _topic_rows(ctx, subject_id: str, skip: set, now: float) -> list[dict]:
    out = []
    for t in tm._topics(ctx, subject_id):
        if t["id"] in skip:
            continue
        lv = tm.level(ctx, t["id"], now)
        d = tm.due(ctx, t["id"], now)
        out.append({"id": t["id"], "title": t["title"], "level": lv, "due": bool(d is not None and d <= now)})
    return out


def _why(ctx, topic_id: str, now: float) -> str:
    from . import drill
    return drill._why(ctx, topic_id, now)


def _course(ctx, c, now: float, today: str) -> dict:
    s = ctx.con.execute("SELECT title FROM teach_subjects WHERE id=?", (c["subject_id"],)).fetchone()
    rows = _topic_rows(ctx, c["subject_id"], _skipped(c), now)
    return {"course": c["id"], "subject": s[0] if s else "", "goal": c["goal"] or "", "by": c["by_date"], "days_left": _days_left(c, today),
            "status": c["status"], "topics": len(rows), "solid": sum(1 for r in rows if r["level"] in SOLID),
            "shaky": sum(1 for r in rows if r["level"] == "shaky"), "due": sum(1 for r in rows if r["due"]),
            "next": _next_bite(ctx, c, today) if c["status"] == "active" else None}


def _lists(ctx, subject_ids: list[str], now: float) -> tuple[list[dict], list[dict]]:
    shaky, due = [], []
    for sid in subject_ids:
        title = (ctx.con.execute("SELECT title FROM teach_subjects WHERE id=?", (sid,)).fetchone() or [""])[0]
        for t in tm._topics(ctx, sid):
            lv = tm.level(ctx, t["id"], now)
            if lv == "shaky":
                shaky.append({"topic": t["title"], "subject": title, "level": lv, "why": _why(ctx, t["id"], now), "score": tm.score(ctx, t["id"], now)})
            d = tm.due(ctx, t["id"], now)
            if d is not None and d <= now:
                due.append({"topic": t["title"], "subject": title, "level": lv, "since": d})
    shaky.sort(key=lambda r: (r["score"], r["topic"]))
    due.sort(key=lambda r: (r["since"], r["topic"]))
    for r in shaky:
        r.pop("score")
    for r in due:
        r.pop("since")
    return shaky[:SHAKY_MAX], due[:DUE_MAX]


def _probes(ctx) -> list[dict]:
    out = []
    for r in ctx.con.execute("SELECT p.topic_id, p.at, t.title FROM teach_probes p JOIN teach_topics t ON t.id=p.topic_id "
                             "WHERE p.set_name='after' AND p.status='done' ORDER BY p.at DESC, p.rowid DESC"):
        if any(o["topic_id"] == r["topic_id"] for o in out):
            continue
        out.append({"topic_id": r["topic_id"], "topic": r["title"], "line": probe.line(ctx, r["topic_id"])})
        if len(out) >= PROBES_MAX:
            break
    for o in out:
        o.pop("topic_id")
    return out


def _drills(ctx, now: float) -> dict:
    rows = ctx.con.execute("SELECT kind, right, wrong FROM teach_drill WHERE status='done' AND updated_at>=?", (now - 7 * DAY,)).fetchall()
    by = {"drill": 0, "review": 0, "probe": 0}
    for r in rows:
        by[r["kind"]] = by.get(r["kind"], 0) + 1
    return {"days": 7, "count": len(rows), "by_kind": by, "right": sum(r["right"] for r in rows), "wrong": sum(r["wrong"] for r in rows)}


def _mistakes(ctx, now: float) -> dict:
    rows = ctx.con.execute("SELECT kind, rights, flagged, at FROM teach_slips").fetchall()
    kept = [r for r in rows if not r["flagged"]]
    by_kind: dict[str, int] = {}
    for r in kept:
        by_kind[r["kind"] or "not filed yet"] = by_kind.get(r["kind"] or "not filed yet", 0) + 1
    pats = slips.patterns(ctx, now)
    return {"total": len(kept), "week": sum(1 for r in kept if r["at"] >= now - 7 * DAY), "retired": sum(1 for r in kept if r["rights"] >= slips.RETIRE_AFTER),
            "flagged": len(rows) - len(kept), "by_kind": by_kind, "pattern": pats[0]["sentence"] if pats else ""}


def overview(ctx) -> dict:
    """The whole Learning view. Empty lists and zeros for a person who has not learned anything with her yet."""
    now = ctx.clock.now()
    today = sgt_date(now)
    cs = ctx.con.execute("SELECT * FROM teach_courses WHERE status IN ('active','paused') ORDER BY status, created_at, id").fetchall()
    subject_ids = list(dict.fromkeys(c["subject_id"] for c in cs))
    shaky, due = _lists(ctx, subject_ids, now)
    return {"courses": [_course(ctx, c, now, today) for c in cs], "shaky": shaky, "due": due, "probes": _probes(ctx),
            "drills": _drills(ctx, now), "mistakes": _mistakes(ctx, now)}
