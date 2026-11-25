"""E4: what already happened to him becomes evidence on the map, without him doing anything.

Class notes: a `lost` item is class_lost, a `remember` item or a section heading is class_seen. The evidence id is
<class id>:<kind>:<8 hex of sha1 of the text>, never an index (notes are remade by class.merge), so running twice adds nothing.
Chat: when the tutor solves a question he asked for help with, the topic gets asked_help, once per topic per day.
"""
from __future__ import annotations

import hashlib
import json
import re

from ..logs import log_event
from . import map as tm
from ..facts import _stem
from .match import topic_and_cost


def _oid(cid: str, kind: str, text: str) -> str:
    return f"{cid}:{kind}:{hashlib.sha1(' '.join(text.split()).encode()).hexdigest()[:8]}"


def _have(ctx, origin: str, origin_id: str) -> bool:
    return ctx.con.execute("SELECT 1 FROM teach_evidence WHERE origin=? AND origin_id=?", (origin, origin_id)).fetchone() is not None


def _norm(s: str) -> str:
    return _stem(re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip())


def auto_link(ctx, subject_id: str) -> str | None:
    """Link a subject to a class subject only when the names are the same word (case and plural aside). Never a guess."""
    sub = ctx.con.execute("SELECT * FROM teach_subjects WHERE id=?", (subject_id,)).fetchone()
    if sub is None:
        return None
    if sub["class_subject"]:
        return sub["class_subject"]
    want = {_norm(sub["key"]), _norm(sub["title"])}
    hit = {r[0] for r in ctx.con.execute("SELECT DISTINCT subject FROM class_sessions") if _norm(r[0]) in want}
    if len(hit) == 1:
        name = hit.pop()
        tm.link_class(ctx, subject_id, name)
        return name
    return None


def _subject_of(ctx, class_subject: str):
    rows = ctx.con.execute("SELECT * FROM teach_subjects WHERE status='active'").fetchall()
    for r in rows:
        if r["class_subject"] and r["class_subject"].lower() == str(class_subject).lower():
            return r
    for r in rows:                                   # not saved yet: link it only if the names are the same word
        if not r["class_subject"] and _norm(r["key"]) == _norm(class_subject) or _norm(r["title"]) == _norm(class_subject):
            if auto_link(ctx, r["id"]):
                return ctx.con.execute("SELECT * FROM teach_subjects WHERE id=?", (r["id"],)).fetchone()
    return None


def _items(notes: dict) -> list[tuple[str, str, str]]:
    """(evidence kind, id kind, text) for every lost item, remember item and heading."""
    out = [("class_lost", "lost", x["text"]) for x in notes.get("lost") or [] if isinstance(x, dict) and x.get("text")]
    out += [("class_seen", "remember", x["text"]) for x in notes.get("remember") or [] if isinstance(x, dict) and x.get("text")]
    out += [("class_seen", "heading", s["heading"]) for s in notes.get("sections") or []
            if isinstance(s, dict) and s.get("heading") and s["heading"] != "Your other notes"]
    return out


async def fill_class(ctx, class_id: str) -> dict:
    row = ctx.con.execute("SELECT subject, notes_json, merged_into FROM class_sessions WHERE id=?", (class_id,)).fetchone()
    if row is None or not row["notes_json"] or row["merged_into"]:
        return {"added": 0, "skipped": "no notes"}
    sub = _subject_of(ctx, row["subject"])
    if sub is None or not ctx.con.execute("SELECT 1 FROM teach_topics WHERE subject_id=? AND status='active' LIMIT 1", (sub["id"],)).fetchone():
        return {"added": 0, "skipped": "no map", "subject": row["subject"]}
    notes = json.loads(row["notes_json"])
    added, usd = 0, 0.0
    for kind, idk, text in _items(notes):
        oid = _oid(class_id, idk, text)
        if _have(ctx, "class", oid):
            continue
        tid, cost = await topic_and_cost(ctx, sub["id"], text)
        usd += cost
        if tid is None:
            continue
        tm.record(ctx, tid, kind, "class", oid, text)
        added += 1
    log_event("TEACH_FILL_CLASS", n=added, usd=usd)
    return {"added": added, "usd": usd}


async def fill_all(ctx) -> dict:
    """Every class with notes. Returns the total and the class subjects that have no map yet."""
    added, usd, skipped = 0, 0.0, []
    for r in ctx.con.execute("SELECT id FROM class_sessions WHERE notes_json IS NOT NULL AND merged_into IS NULL ORDER BY started_at").fetchall():
        res = await fill_class(ctx, r["id"])
        added += res["added"]
        usd += res.get("usd", 0.0)
        if res.get("skipped") == "no map" and res["subject"] not in skipped:
            skipped.append(res["subject"])
    return {"added": added, "usd": usd, "skipped": skipped}


async def help_evidence(ctx, key: dict, text: str = "") -> bool:
    """The tutor just solved a question he asked help with: record asked_help on the topic it belongs to, once a day."""
    if not ctx.con.execute("SELECT 1 FROM teach_topics WHERE status='active' LIMIT 1").fetchone():
        return False
    q = " ".join(f"{key.get('question') or ''} {key.get('idea') or ''}".split()) or text
    tid, _ = await topic_and_cost(ctx, None, q)
    if tid is None:
        return False
    day = int((ctx.clock.now() + 8 * 3600) // 86400)
    oid = f"help:{tid}:{day}"
    if _have(ctx, "chat", oid):
        return False
    tm.record(ctx, tid, "asked_help", "chat", oid, str(key.get("question") or text))
    return True
