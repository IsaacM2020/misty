"""E17: before and after. Three sets of three bank questions per topic (one per level 1 to 3, never a plan question, never shared
between sets), asked as a quiet drill (kind `probe`). One row in `teach_probes` per set asked or skipped. The line after a lesson is
pure text from those rows. Nothing here knows a subject or a person.

Row status: done | skipped | offered (asked, no answer yet) | left (said not now: waits for the next drill, `after` only).
"""
from __future__ import annotations

import json
import re
import secrets

from ..logs import log_event
from . import bank
from . import map as tm

DAY = 86400
LEVELS = (1, 2, 3)
SETS = ("before", "after", "week")
PER_LEVEL = 3                 # three sets of one per level: a topic needs 3 usable bank questions at every level (9 in all)
OFFER_SECONDS = 30 * 60       # an offer he does not answer in half an hour is dropped for the chat
LEFT_DAYS = 3                 # an `after` he said not now to stays on the next drill for this long
WEEK_DAYS = 6

OFFER_BEFORE = "Three quick ones to see where you are? Say 'just teach me' to skip."
OFFER_AFTER = "Three quick ones to see what stuck? Now, or it waits for your next drill."

_YES = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|go|go on|go ahead|do it|let'?s go|let'?s do it|please|alright|fine|why not)\b", re.I)
_NO = re.compile(r"^(?:just teach me|just the lesson|skip(?: it)?|no|nope|nah|not now|no thanks|later|not today)\b", re.I)

PROBE_RULE = """A quick check is running (the teach tool). It is three questions in a row, not a lesson. Be quiet and short: after each answer say only the verdict
in one or two words ("right" or "not quite"), never the answer, never a hint, never a reason, then the next question exactly once. If the result says
finished, say how many he got out of three and stop; if it has lesson_started, then go on with that lesson's first step."""


# ---- picking the sets ----------------------------------------------------------------------------------------------
def eligible(ctx, topic_id: str) -> bool:
    """A topic can be probed only with 3 usable bank questions at every level (plan questions do not count)."""
    c = bank.counts(ctx, topic_id)
    return all(c.get(lv, 0) >= PER_LEVEL for lv in LEVELS) and sum(c.values()) >= PER_LEVEL * len(LEVELS)


def _rows(ctx, topic_id: str, set_name: str | None = None, status: str | None = None) -> list:
    q, a = "SELECT * FROM teach_probes WHERE topic_id=?", [topic_id]
    if set_name:
        q, a = q + " AND set_name=?", a + [set_name]
    if status:
        q, a = q + " AND status=?", a + [status]
    return ctx.con.execute(q + " ORDER BY at, rowid", a).fetchall()


def _used_elsewhere(ctx, topic_id: str, set_name: str) -> set[str]:
    out: set[str] = set()
    for r in _rows(ctx, topic_id):
        if r["set_name"] != set_name and r["status"] in ("done", "offered", "left"):
            out |= set(json.loads(r["qids"] or "[]"))
    return out


def pick_set(ctx, topic_id: str, set_name: str) -> list[str] | None:
    """One usable bank question per level, none used by another set of this topic. None when the topic cannot be probed."""
    if set_name not in SETS or not eligible(ctx, topic_id):
        return None
    skip = _used_elsewhere(ctx, topic_id, set_name)
    chosen: list[str] = []
    for lv in LEVELS:
        got = [q for q in ctx.con.execute(
            "SELECT id FROM teach_questions WHERE topic_id=? AND level=? AND source='bank' AND status='usable' ORDER BY made_at, id", (topic_id, lv))
            if q["id"] not in skip and q["id"] not in chosen]
        if not got:
            return None
        chosen.append(got[0]["id"])
    return chosen if valid(ctx, topic_id, set_name, chosen) else None


def valid(ctx, topic_id: str, set_name: str, qids: list[str]) -> bool:
    """The guard run again before any set is asked: three questions, one per level, all usable bank questions of the topic,
    none of them in another set of the topic."""
    if len(qids) != len(LEVELS) or len(set(qids)) != len(qids):
        return False
    if set(qids) & _used_elsewhere(ctx, topic_id, set_name):
        return False
    levels = []
    for qid in qids:
        r = ctx.con.execute("SELECT topic_id, level, source, status FROM teach_questions WHERE id=?", (qid,)).fetchone()
        if r is None or r["topic_id"] != topic_id or r["source"] != "bank" or r["status"] != "usable":
            return False
        levels.append(r["level"])
    return sorted(levels) == list(LEVELS)


# ---- rows ----------------------------------------------------------------------------------------------------------
def _put(ctx, topic_id: str, set_name: str, qids: list[str], status: str, lesson_id: str = "", score: int = 0, out_of: int = 0) -> str:
    pid = "pb_" + secrets.token_hex(6)
    with ctx.con:
        ctx.con.execute("INSERT INTO teach_probes(id, topic_id, set_name, qids, score, out_of, lesson_id, at, status) VALUES (?,?,?,?,?,?,?,?,?)",
                        (pid, topic_id, set_name, json.dumps(qids), score, out_of, lesson_id, ctx.clock.now(), status))
    return pid


def _set(ctx, pid: str, **cols) -> None:
    with ctx.con:
        ctx.con.execute("UPDATE teach_probes SET " + ", ".join(f"{k}=?" for k in cols) + " WHERE id=?", [*cols.values(), pid])


def _title(ctx, topic_id: str) -> str:
    t = ctx.con.execute("SELECT title FROM teach_topics WHERE id=?", (topic_id,)).fetchone()
    return t["title"] if t else ""


# ---- offers --------------------------------------------------------------------------------------------------------
def offer_before(ctx, topic_id: str) -> dict | None:
    """`teach start` on a topic he has not really been taught: offer the quick check first. None = just start the lesson."""
    now = ctx.clock.now()
    if tm.level(ctx, topic_id, now) not in ("unknown", "seen"):
        return None
    if any(r["status"] in ("done", "skipped") for r in _rows(ctx, topic_id, "before")):
        return None
    qids = pick_set(ctx, topic_id, "before")
    if qids is None:
        return None                                              # under 9 usable: no probe, and the topic is not in the bar
    old = [r for r in _rows(ctx, topic_id, "before", "offered")]
    if old:
        _set(ctx, old[-1]["id"], at=now, qids=json.dumps(qids))
    else:
        _put(ctx, topic_id, "before", qids, "offered")
    log_event("TEACH_PROBE", code="offer_before")
    return {"ok": True, "probe_offer": {"topic": _title(ctx, topic_id), "set": "before", "of": len(qids)}, "say": OFFER_BEFORE}


def offer_after(ctx, topic_id: str, lesson_id: str) -> dict | None:
    """A lesson just finished: offer the `after` set once for it."""
    if any(r["lesson_id"] == lesson_id for r in _rows(ctx, topic_id, "after")):
        return None
    qids = pick_set(ctx, topic_id, "after")
    if qids is None:
        return None
    _put(ctx, topic_id, "after", qids, "offered", lesson_id)
    log_event("TEACH_PROBE", code="offer_after")
    return {"topic": _title(ctx, topic_id), "set": "after", "of": len(qids), "say": OFFER_AFTER}


def pending(ctx):
    """The one offer waiting for his yes or no (asked within the last half hour), or None."""
    r = ctx.con.execute("SELECT * FROM teach_probes WHERE status='offered' AND at>=? ORDER BY at DESC, rowid DESC LIMIT 1",
                        (ctx.clock.now() - OFFER_SECONDS,)).fetchone()
    return r


async def on_offer_message(ctx, text: str) -> dict | None:
    """His reply to an offer, before the model runs. None = not an answer to an offer."""
    from . import drill, live
    r = pending(ctx)
    t = " ".join((text or "").split())
    if r is None or not t or len(t.split()) > 6:
        return None
    yes, no = bool(_YES.search(t)), bool(_NO.search(t))
    if not yes and not no:
        return None
    topic, name = r["topic_id"], r["set_name"]
    if yes and not no:
        qids = pick_set(ctx, topic, name)
        if qids is not None:
            _set(ctx, r["id"], qids=json.dumps(qids))
            res = drill.begin_probe(ctx, topic, name, qids, then_topic=topic if name == "before" else "", probe_id=r["id"])
            if res.get("ok"):
                return res
    if name == "before":                                         # no, or it cannot run: skipped, and the lesson starts
        _set(ctx, r["id"], status="skipped", qids="[]", at=ctx.clock.now())
        log_event("TEACH_PROBE", code="skipped")
        res = await live.start(ctx, topic)
        res["probe_skipped"] = True
        return res
    _set(ctx, r["id"], status="left")
    return {"ok": True, "probe": {"left": True, "topic": _title(ctx, topic)}, "say": "No problem, it will come with your next drill."}


# ---- the drill hook ------------------------------------------------------------------------------------------------
def week_due(ctx, topic_id: str) -> bool:
    now = ctx.clock.now()
    after = _rows(ctx, topic_id, "after", "done")
    if not after or now - after[-1]["at"] < WEEK_DAYS * DAY:
        return False
    if any(r["at"] > after[-1]["at"] for r in _rows(ctx, topic_id, "week", "done")):
        return False
    return True                      # the next drill on this topic, six days or more after the after set, is the week probe


def for_drill(ctx, topic_id: str) -> tuple[str, str, list[str]] | None:
    """(set_name, row id or '', qids) when a drill on this topic should be a probe: an `after` left for later, else the `week` one."""
    now = ctx.clock.now()
    for r in reversed(_rows(ctx, topic_id, "after")):
        if r["status"] in ("offered", "left") and now - r["at"] <= LEFT_DAYS * DAY and (r["status"] == "left" or now - r["at"] > OFFER_SECONDS):
            qids = pick_set(ctx, topic_id, "after")
            if qids is not None:
                _set(ctx, r["id"], qids=json.dumps(qids))
                return "after", r["id"], qids
    if week_due(ctx, topic_id):
        qids = pick_set(ctx, topic_id, "week")
        if qids is not None:
            return "week", "", qids
    return None


# ---- the end of a probe --------------------------------------------------------------------------------------------
def finish(ctx, drill_row, state: dict) -> dict:
    """Called by the drill machine when a probe ends. Writes the row, returns what the result carries."""
    x = json.loads(drill_row["extra"] or "{}")
    name, topic = x.get("set", ""), drill_row["topic_id"]
    pid = x.get("probe_id") or ""
    qids = json.loads(drill_row["qids"] or "[]")
    if state["status"] == "done":
        if pid:
            _set(ctx, pid, status="done", score=state["right"], out_of=state["n"], qids=json.dumps(qids), at=ctx.clock.now())
        else:
            _put(ctx, topic, name, qids, "done", "", state["right"], state["n"])
        log_event("TEACH_PROBE", code="done_" + name, n=state["right"])
        out = {"set": name, "right": state["right"], "of": state["n"]}
        if name in ("after", "week"):
            out["line"] = line(ctx, topic)
        if x.get("then"):
            out["then"] = x["then"]
        return out
    if pid:                                                      # stopped half way: a before is skipped, an after waits
        _set(ctx, pid, status="skipped" if name == "before" else "left", qids="[]" if name == "before" else json.dumps(qids), at=ctx.clock.now())
    return {"set": name, "stopped": True}


def line(ctx, topic_id: str) -> str:
    """'1 of 3 before, 3 of 3 now.' Plain text from the rows. With no `before` it says only the score now, never that he learned."""
    after = _rows(ctx, topic_id, "after", "done")
    if not after:
        return ""
    a = after[-1]
    before = _rows(ctx, topic_id, "before", "done")
    s = f"{before[-1]['score']} of {before[-1]['out_of']} before, {a['score']} of {a['out_of']} now." if before else f"{a['score']} of {a['out_of']} now."
    week = [r for r in _rows(ctx, topic_id, "week", "done") if r["at"] > a["at"]]
    if week:
        s += f" {week[-1]['score']} of {week[-1]['out_of']} a week later."
    return s
