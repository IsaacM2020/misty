"""Feedback on what she told him on her own (Season 3, Day 6). Four kinds come in:
  up / down   a thumb on a line (a button, or him saying it was useful / not useful)
  ignored     a line nobody reacted to for a few hours counts as a soft thumbs-down
  missed      "you should have told me about X"
  too_much    "that's too much"
Every miss is saved with what she knew at that moment (the line, what the gate saw, her cares and follows), so the next
step can turn it into a lesson and later replay it. Nothing here changes how she behaves yet."""
from __future__ import annotations

import json
import secrets

from . import cares, follows
from .logs import log_event

IGNORE_AFTER_S = 3 * 3600
RECENT_S = 6 * 3600
THUMBS = ("up", "down")
KINDS = ("up", "down", "missed", "too_much")
NOTE_MAX = 300


def _clip(s: str, n: int = NOTE_MAX) -> str:
    return " ".join(str(s or "").split())[:n]


def snapshot(ctx, told=None) -> str:
    """What she knew at this moment, as plain JSON."""
    con = ctx.con
    snap = {
        "cares": [f"{c['key']} ({cares.LEVEL_WORDS.get(c['weight'], 'matters')})" for c in cares.active(con)][:20],
        "following": [f["text"] for f in follows.active(con)][:10],
        "recent_told": [r["said"] for r in con.execute("SELECT said FROM told ORDER BY at DESC LIMIT 5")],
    }
    if told is not None:
        snap["line"] = {"said": told["said"], "text": told["text"], "source": told["source_name"], "how": told["how"],
                        "urgency": told["urgency"], "why": told["why"], "waited_s": told["waited_s"], "topic": told["topic_text"],
                        "seen": json.loads(told["seen"]) if told["seen"] else None}
    else:
        from . import gate
        snap["seen"] = gate.seen_now(ctx, ctx.clock.now())
        snap["waiting"] = [r["text"] for r in con.execute("SELECT text FROM held WHERE status='waiting' ORDER BY at LIMIT 5")]
    return json.dumps(snap)


def _mistake(ctx, kind: str, told, about: str, note: str) -> str:
    mid = "ms_" + secrets.token_hex(5)
    with ctx.con:
        ctx.con.execute("INSERT INTO mistakes(id, at, kind, told_id, about, note, snapshot) VALUES (?,?,?,?,?,?,?)",
                        (mid, ctx.clock.now(), kind, told["id"] if told is not None else None, _clip(about, 140), _clip(note),
                         snapshot(ctx, told)))
    log_event("FEEDBACK_SAVED", code=kind)
    return mid


def _row(con, told_id: str):
    return con.execute("SELECT * FROM told WHERE id=?", (told_id,)).fetchone()


def rate(ctx, told_id: str, value: str, note: str = "") -> dict:
    """A thumb on one line. Changing your mind is fine: the newer thumb wins."""
    if value not in THUMBS:
        return {"ok": False, "error": "a thumb is up or down"}
    r = _row(ctx.con, told_id)
    if r is None:
        return {"ok": False, "error": "no such line"}
    with ctx.con:
        ctx.con.execute("UPDATE told SET feedback=?, feedback_at=? WHERE id=?", (value, ctx.clock.now(), told_id))
        ctx.con.execute("DELETE FROM mistakes WHERE told_id=? AND kind IN ('down','ignored') AND lesson_id IS NULL", (told_id,))
    if value == "down":
        _mistake(ctx, "down", _row(ctx.con, told_id), r["said"], note)
    log_event("FEEDBACK_RATED", code=value)
    return {"ok": True, "value": value, "line": r["said"]}


def interrupted(ctx, told_id: str) -> dict:
    """F4: he talked over a line she began herself. The line is marked `interrupted`: not a thumbs down by itself, no mistake saved,
    and never swept as `ignored` (he did react). A thumb he gives later replaces it; a thumb already given is kept."""
    r = _row(ctx.con, told_id) if told_id else None
    if r is None:
        return {"ok": False, "error": "no such line"}
    if r["feedback"] is None:
        with ctx.con:
            ctx.con.execute("UPDATE told SET feedback='interrupted', feedback_at=? WHERE id=?", (ctx.clock.now(), told_id))
        log_event("FEEDBACK_INTERRUPTED", code="update")
    return {"ok": True, "value": r["feedback"] or "interrupted"}


def last_line(con, now: float):
    """The line he most likely means: the newest thing she told him on her own in the last few hours."""
    return con.execute("SELECT * FROM told WHERE at >= ? ORDER BY at DESC LIMIT 1", (now - RECENT_S,)).fetchone()


def rate_last(ctx, value: str, note: str = "") -> dict:
    r = last_line(ctx.con, ctx.clock.now())
    if r is None:
        return {"ok": False, "error": "she has not told him anything on her own lately"}
    return rate(ctx, r["id"], value, note)


def sweep_ignored(ctx) -> int:
    """Lines nobody reacted to for a few hours become a soft thumbs-down (and a soft mistake)."""
    now = ctx.clock.now()
    rows = ctx.con.execute("SELECT * FROM told WHERE feedback IS NULL AND at < ?", (now - IGNORE_AFTER_S,)).fetchall()
    for r in rows:
        with ctx.con:
            ctx.con.execute("UPDATE told SET feedback='ignored', feedback_at=? WHERE id=?", (now, r["id"]))
        _mistake(ctx, "ignored", r, r["said"], "")
    return len(rows)


def missed(ctx, about: str, note: str = "") -> dict:
    if not _clip(about):
        return {"ok": False, "error": "what should she have told him about?"}
    _mistake(ctx, "missed", None, about, note)
    return {"ok": True, "kind": "missed", "about": _clip(about, 140)}


def too_much(ctx, note: str = "") -> dict:
    from . import gate
    _mistake(ctx, "too_much", last_line(ctx.con, ctx.clock.now()), "told him too much", note)
    return {"ok": True, "kind": "too_much", **gate.describe(ctx)}


def record(ctx, kind: str = "", about: str = "", note: str = "", wanted: str = "") -> dict:
    """The chat tool: one entry point for the four kinds, and for "you did the wrong thing" (`wanted`, card B2b)."""
    if wanted:
        from . import wrong_picks
        return wrong_picks.record(ctx, wanted, note or about)
    if kind in THUMBS:
        return rate_last(ctx, kind, note or about)
    if kind == "missed":
        return missed(ctx, about, note)
    if kind == "too_much":
        return too_much(ctx, note or about)
    return {"ok": False, "error": "feedback is up, down, missed or too_much"}


def recent_block(con, now: float) -> str:
    """What she told him on her own lately, so she knows what a reaction like 'nice' or 'not useful' is about."""
    rows = con.execute("SELECT said, source_name, how, feedback FROM told WHERE at >= ? ORDER BY at DESC LIMIT 3", (now - RECENT_S,)).fetchall()
    if not rows:
        return ""
    lines = [f"- {r['said']}" + (f" ({r['source_name']})" if r["source_name"] else "") + (f" [he gave it: {r['feedback']}]" if r["feedback"] else "")
             for r in rows]
    return "Things you told him on your own lately (newest first):\n" + "\n".join(lines)


def listing(con, n: int = 30) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT id, at, topic_text, said, source_name, url, how, urgency, why, feedback FROM told "
                                         "ORDER BY at DESC LIMIT ?", (n,))]
