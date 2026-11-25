"""The map (E1): subjects, topics, needs, evidence, and the exact scoring. Pure code, no model call.

How well he knows a topic is arithmetic on his evidence, never a model's opinion. Each piece of evidence has a
weight and fades by half every 21 days. The sum is the score. The level and the next review time follow from it.
Nothing here knows a subject, a school or a person: a subject is a key and a title, a topic is a key and a cite.
"""
from __future__ import annotations

import secrets

from ..clock import sgt_date
from ..errors import Fatal
from ..facts import _stem, norm_key
from ..logs import log_event

DAY = 86400.0

# Every number that decides a level lives here, each with its reason.
TUNING = {
    "half_life_days": 21.0,   # an answer counts half as much after three weeks: forgetting is what drives review
    "solid_score": 1.0,       # one clean right answer (1.0) is the least that can be called solid
    "mastered_score": 2.5,    # about three clean answers, so a lucky streak of two never counts as mastered
    "mastered_days": 2,       # clean answers must come on different days: cramming one evening is not mastery
}

# Evidence kind -> weight. The one table.
WEIGHTS = {
    "right_alone": 1.0,       # right with no hint
    "right_helped": 0.4,      # right after a hint or a second explanation
    "partly": 0.1,            # part of the marks
    "wrong": -1.0,            # wrong
    "class_lost": -0.6,       # his class notes say he got lost here
    "asked_help": -0.3,       # he asked for help on it
    "taught": 0.0,            # a lesson on it was given
    "class_seen": 0.0,        # it came up in a class
}
ANSWERS = ("right_alone", "right_helped", "partly", "wrong")   # kinds that are an answer he gave
ORIGINS = ("lesson", "drill", "exam", "class", "chat")
NEED_ORIGINS = ("model", "him")
REVIEW_DAYS = [1, 3, 7, 16, 35]   # days until the next review, by streak of clean answers (35 from then on)


# ---- subjects and topics -----------------------------------------------------------------------------------------
def _same(con, table: str, key: str, where: str = "", args: tuple = ()):
    """The row with this key, or one whose key has the same stem ("long runs" is "long run")."""
    k = norm_key(key)
    if not k:
        return None, ""
    st = _stem(k)
    for r in con.execute(f"SELECT * FROM {table} WHERE status!='dropped'{where} ORDER BY created_at", args):
        if r["key"] == k or _stem(r["key"]) == st:
            return r, k
    return None, k


def add_subject(ctx, key: str, title: str) -> dict:
    """Add a subject, or return the one with the same key or stem."""
    old, k = _same(ctx.con, "teach_subjects", key)
    title = " ".join(str(title).split())[:120]
    if not k or not title:
        raise Fatal("BAD_SUBJECT")
    if old is not None:
        return {"id": old["id"], "key": old["key"], "action": "exists"}
    sid = "ts_" + secrets.token_hex(6)
    with ctx.con:
        ctx.con.execute("INSERT INTO teach_subjects(id, key, title, status, created_at) VALUES (?,?,?,'active',?)",
                        (sid, k, title, ctx.clock.now()))
    log_event("TEACH_SUBJECT_ADDED", n=1)
    return {"id": sid, "key": k, "action": "added"}


def add_topic(ctx, subject_id: str, key: str, title: str, cite: dict) -> dict:
    """Add a topic with its cite, or update the one in this subject with the same key or stem. No embedding merge:
    near-duplicates are merged in E3 by the model with the list in front of it."""
    k = norm_key(key)
    title = " ".join(str(title).split())[:160]
    source_id = str((cite or {}).get("source_id") or "").strip()
    quote = " ".join(str((cite or {}).get("quote") or "").split())
    try:
        chunk_no = int((cite or {}).get("chunk_no"))
    except (TypeError, ValueError):
        chunk_no = None
    if not k or not title or not source_id or not quote or chunk_no is None:
        raise Fatal("BAD_TOPIC")
    old, k = _same(ctx.con, "teach_topics", k, " AND subject_id=?", (subject_id,))
    with ctx.con:
        if old is not None:
            ctx.con.execute("UPDATE teach_topics SET title=?, source_id=?, chunk_no=?, quote=? WHERE id=?",
                            (title, source_id, chunk_no, quote, old["id"]))
            return {"id": old["id"], "key": old["key"], "action": "updated"}
        tid = "tt_" + secrets.token_hex(6)
        ctx.con.execute("INSERT INTO teach_topics(id, subject_id, key, title, source_id, chunk_no, quote, status, "
                        "created_at) VALUES (?,?,?,?,?,?,?,'active',?)",
                        (tid, subject_id, k, title, source_id, chunk_no, quote, ctx.clock.now()))
    log_event("TEACH_TOPIC_ADDED", n=1)
    return {"id": tid, "key": k, "action": "added"}


# ---- needs -------------------------------------------------------------------------------------------------------
def _key(con, tid: str) -> str:
    r = con.execute("SELECT key FROM teach_topics WHERE id=?", (tid,)).fetchone()
    return r["key"] if r else tid


def _path(con, start: str, goal: str):
    """Follow 'needs' links from start; return the list of topic ids from start to goal, or None."""
    stack, seen = [(start, [start])], set()
    while stack:
        cur, path = stack.pop()
        if cur == goal:
            return path
        if cur in seen:
            continue
        seen.add(cur)
        for r in con.execute("SELECT needs_id FROM teach_needs WHERE topic_id=? AND status='active'", (cur,)):
            stack.append((r["needs_id"], path + [r["needs_id"]]))
    return None


def add_need(ctx, topic_id: str, needs_id: str, origin: str = "model") -> dict:
    """topic_id needs needs_id first. Refused if it would make a loop; the chain is named."""
    if origin not in NEED_ORIGINS:
        raise Fatal("BAD_NEED")
    back = _path(ctx.con, needs_id, topic_id)
    if back is not None:
        chain = [_key(ctx.con, topic_id)] + [_key(ctx.con, t) for t in back]
        why = "that would make a loop: " + " needs ".join(chain)
        return {"ok": False, "chain": chain, "why": why}
    with ctx.con:
        ctx.con.execute("INSERT INTO teach_needs(topic_id, needs_id, origin, status) VALUES (?,?,?, 'active') "
                        "ON CONFLICT(topic_id, needs_id) DO UPDATE SET origin=excluded.origin, status='active'",
                        (topic_id, needs_id, origin))
    return {"ok": True}


# ---- evidence and the score --------------------------------------------------------------------------------------
def record(ctx, topic_id: str, kind: str, origin: str, origin_id: str = "", note: str = "") -> int:
    """Write one piece of evidence, with the weight fixed by its kind. Returns the evidence id."""
    if kind not in WEIGHTS or origin not in ORIGINS:
        raise Fatal("BAD_EVIDENCE")
    with ctx.con:
        cur = ctx.con.execute("INSERT INTO teach_evidence(topic_id, at, kind, weight, origin, origin_id, note) "
                              "VALUES (?,?,?,?,?,?,?)",
                              (topic_id, ctx.clock.now(), kind, WEIGHTS[kind], origin, str(origin_id)[:80],
                               " ".join(str(note).split())[:200]))
    log_event("TEACH_EVIDENCE", n=1)
    return cur.lastrowid


def _rows(ctx, topic_id: str) -> list:
    return ctx.con.execute("SELECT * FROM teach_evidence WHERE topic_id=? AND note NOT LIKE 'cancelled%' ORDER BY at, id", (topic_id,)).fetchall()


def _score(rows, now: float) -> float:
    half = TUNING["half_life_days"]
    return sum(r["weight"] * 0.5 ** (max(0.0, now - r["at"]) / DAY / half) for r in rows)


def score(ctx, topic_id: str, now: float) -> float:
    return _score(_rows(ctx, topic_id), now)


def _level(rows, now: float) -> str:
    if not rows:
        return "unknown"
    if all(r["weight"] == 0 for r in rows):
        return "seen"
    s = _score(rows, now)
    answers = [r for r in rows if r["kind"] in ANSWERS]
    if (answers and answers[-1]["kind"] == "wrong") or s < TUNING["solid_score"]:
        return "shaky"
    days = {sgt_date(r["at"]) for r in rows if r["kind"] == "right_alone"}
    if s >= TUNING["mastered_score"] and len(days) >= TUNING["mastered_days"]:
        return "mastered"
    return "solid"


def level(ctx, topic_id: str, now: float) -> str:
    """unknown | seen | shaky | solid | mastered. One wrong answer always drops a topic to shaky until he is right."""
    return _level(_rows(ctx, topic_id), now)


def _streak(rows) -> int:
    n = 0
    for r in reversed([r for r in rows if r["kind"] in ANSWERS]):
        if r["kind"] != "right_alone":
            break
        n += 1
    return n


def due(ctx, topic_id: str, now: float) -> float | None:
    """When he should be asked again. None for a topic never taught (unknown or seen: untaught is not forgotten)."""
    rows = _rows(ctx, topic_id)
    lv = _level(rows, now)
    if lv in ("unknown", "seen"):
        return None
    answers = [r for r in rows if r["kind"] in ANSWERS]
    if not answers:
        return now                                   # only negative evidence and no answer yet: due now
    streak = _streak(rows)
    gap = REVIEW_DAYS[min(max(streak, 1), len(REVIEW_DAYS)) - 1]
    return answers[-1]["at"] + gap * DAY


# ---- reading the map ---------------------------------------------------------------------------------------------
def _topics(ctx, subject_id: str) -> list:
    return ctx.con.execute("SELECT * FROM teach_topics WHERE subject_id=? AND status='active' ORDER BY created_at, id",
                           (subject_id,)).fetchall()


def weakest(ctx, subject_id: str, n: int, now: float) -> list[dict]:
    """Shaky topics, lowest score first, ties to the most recent bad evidence. Never unknown or seen."""
    out = []
    for t in _topics(ctx, subject_id):
        rows = _rows(ctx, t["id"])
        if _level(rows, now) != "shaky":
            continue
        bad = [r["at"] for r in rows if r["weight"] < 0]
        out.append({"topic_id": t["id"], "key": t["key"], "title": t["title"], "score": _score(rows, now),
                    "last_bad": max(bad) if bad else 0.0})
    out.sort(key=lambda d: (round(d["score"], 9), -d["last_bad"]))
    return out[:max(0, n)]


def ready(ctx, subject_id: str, now: float) -> list[dict]:
    """Topics not yet solid whose needs are all solid or mastered. A topic with no needs is ready."""
    topics = _topics(ctx, subject_id)
    lv = {t["id"]: _level(_rows(ctx, t["id"]), now) for t in topics}
    out = []
    for t in topics:
        if lv[t["id"]] in ("solid", "mastered"):
            continue
        needs = ctx.con.execute("SELECT n.needs_id FROM teach_needs n JOIN teach_topics p ON p.id=n.needs_id "
                                "WHERE n.topic_id=? AND n.status='active' AND p.status='active'", (t["id"],)).fetchall()
        if all(lv.get(r["needs_id"]) in ("solid", "mastered") or
               _level(_rows(ctx, r["needs_id"]), now) in ("solid", "mastered") for r in needs):
            out.append({"topic_id": t["id"], "key": t["key"], "title": t["title"], "level": lv[t["id"]]})
    return out


def link_class(ctx, subject_id: str, class_subject: str) -> dict:
    """The one saved link from this subject to the subject name his classes use. Empty clears it."""
    name = " ".join(str(class_subject or "").split())[:40]
    with ctx.con:
        ctx.con.execute("UPDATE teach_subjects SET class_subject=? WHERE id=?", (name, subject_id))
    return {"ok": True, "class_subject": name}
