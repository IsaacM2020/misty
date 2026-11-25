"""E9: "teach me this". With a page, PDF or class open, he asks to be taught it and a lesson starts on the part he is on.

Two paths meet here. The old whiteboard path (look, solve, teach one problem) stays for a problem on screen. The new path
(source, map, plan, lesson) runs when what is open is something to be taught. Which one is decided in code, never by the model.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import tutor
from ..errors import Fatal, MistyError
from ..facts import norm_key
from ..logs import log_event
from ..openrouter import user_approved
from . import build as tb
from . import fill, match, sources
from . import live as tlive
from . import map as tm

INLINE_CHARS = 12_000            # a source this small is mapped while he waits; a bigger one runs in the background

TEACH_ME = re.compile(r"\b(?:teach|walk|take) (?:me|us)(?: through| over)? (?:this|that|it|what|the (?:page|chapter|section|article|notes))\b"
                      r"|\bgo (?:through|over) (?:this|that|it|the (?:page|chapter|section|article|notes))\b", re.I)
CLASS_REF = re.compile(r"\b(?:class|lecture|what we (?:did|covered|learn(?:ed|t)|went over))\b", re.I)
QUESTION = re.compile(r"\b(question|problem|q ?\d+|part \(?[a-h]\)?|number \d+|exercise)\b", re.I)
WORKSHEET = re.compile(r"worksheet|problems?|exercises?|questions?|homework|past ?paper|\bws\b|quiz|assignment", re.I)


def which_path(text: str, doc: str = "") -> str:
    """`teach` (the new path), `solve` (the old whiteboard path), `ask` (a document is open and he said "help me with this":
    one short question), or `none` (not about either). `doc` is what he has open (a path or link), empty when nothing is."""
    t = text or ""
    teachme = bool(TEACH_ME.search(t))
    helps = tutor.wants_help(t)
    if not (teachme or helps):
        return "none"
    if QUESTION.search(t):
        return "solve"                                   # a question number means one problem
    if teachme:
        return "teach" if (doc or CLASS_REF.search(t)) else "solve"
    if not doc:
        return "solve"
    return "solve" if WORKSHEET.search(os.path.basename(doc.rstrip("/"))) else "ask"


def _subject_name(ctx, src: dict) -> tuple[str, str]:
    """(key, title) of the subject a source goes under: a class uses the name his classes use (so E4's link and evidence line up),
    a file or page uses its own name."""
    row = ctx.con.execute("SELECT * FROM teach_sources WHERE id=?", (src["id"],)).fetchone()
    if row["class_id"]:
        name = ctx.con.execute("SELECT subject FROM class_sessions WHERE id=?", (row["class_id"],)).fetchone()[0]
    else:
        name = Path(str(row["title"]).rstrip("/")).stem or str(row["title"])
        name = re.sub(r"[-_]+", " ", name).strip()
    return (norm_key(name) or "course"), name


def _topics_of(ctx, subject_id: str, source_id: str) -> list[dict]:
    mine = {r[0] for r in ctx.con.execute("SELECT id FROM teach_topics WHERE source_id=? AND subject_id=? AND status='active'", (source_id, subject_id))}
    return [t for t in tb.ordered(ctx, subject_id) if t["topic_id"] in mine]


STOP = frozenset("the a an of on in is to and for with about part bit section page chapter this that it me my you can please from at as by be do".split())


def _content(text: str) -> str:
    """His words without the filler, so a search for "the part about X" is a search for X (a filler word would match every chunk)."""
    return " ".join(w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOP)


def _stems(text: str) -> set[str]:
    return {w[:4] for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def _best(cands: list[dict], words: str) -> dict:
    """Several topics can cite the same chunk: the one whose title and quote share the most word stems with his words."""
    w = _stems(words)
    return max(cands, key=lambda t: len(w & _stems(t["title"] + " " + t["quote"])))   # a tie keeps the earlier topic


def _from_chunks(ctx, source_id: str, topics: list[dict], words: str) -> dict | None:
    q = _content(words)
    for no, _ in (sources.search(ctx, source_id, q, 3) if q else []):
        cands = [t for t in topics if t["chunk_no"] == no]
        if cands:
            return _best(cands, words)
    return None


async def _pick(ctx, subject_id: str, source_id: str, topics: list[dict], part: str) -> dict:
    words = " ".join((part or "").split())
    if words:
        t = _from_chunks(ctx, source_id, topics, words)
        if t is not None:
            return t
        with user_approved():                              # one small call, only when words did not find it
            tid = await match.topic_for(ctx, subject_id, words)
        for t in topics:
            if t["topic_id"] == tid:
                return t
    near = " ".join((getattr(ctx, "open_near", "") or "").split())
    if not words and near:
        t = _from_chunks(ctx, source_id, topics, near)
        if t is not None:
            return t
    ready = {r["topic_id"] for r in tm.ready(ctx, subject_id, ctx.clock.now())}
    for t in topics:
        if t["topic_id"] in ready:
            return t
    return topics[0]


async def start_this(ctx, what: str = "this", part: str = "", on_status=None) -> dict:
    """Add the source, make its map if it has none, pick the part, start the lesson."""
    try:
        src = await sources.add(ctx, (what or "this").strip() or "this", doc=ctx.open_doc)
    except Fatal as e:
        return {"ok": False, "error": str(e)}
    sid = src["id"]
    m = tb.map_state(ctx, sid)
    mapped_now = False
    if m and m["status"] in ("queued", "running"):
        return {"ok": True, "queued": True, "say": "I'm still mapping that. I'll tell you when the map is ready."}
    key, title = _subject_name(ctx, src)
    if m and m["status"] == "too_big":
        return {"ok": False, "error": m["why"] or "that has too many topics: pick which part to start with"}
    if m is None or m["status"] == "failed":
        if src["chars"] > INLINE_CHARS:
            r = tb.queue_map(ctx, sid, key, title, tell=True)
            fill.auto_link(ctx, tm.add_subject(ctx, key, title)["id"])
            return {"ok": True, "queued": True, "source": src["title"], "subject": r["subject"],
                    "say": "That's big, so I'll map it in the background and tell you when the map is ready."}
        subj = tm.add_subject(ctx, key, title)
        fill.auto_link(ctx, subj["id"])
        if on_status is not None:
            await on_status("One moment, I'm mapping this…")
        with user_approved():
            m = await tb.build_map(ctx, sid, subj["id"])
        mapped_now = True
        if m["status"] != "done":
            return {"ok": False, "error": f"I couldn't map that: {m['why'] or 'the model did not help'}"}
    subject_id = m["subject_id"]
    topics = _topics_of(ctx, subject_id, sid)
    if not topics:
        return {"ok": False, "error": "I found no topics in that to teach"}
    pick = await _pick(ctx, subject_id, sid, topics, part)
    after = topics[[t["topic_id"] for t in topics].index(pick["topic_id"]) + 1:]
    res = await tlive.start(ctx, pick["topic_id"])
    log_event("TEACH_THIS", code="start", n=len(topics))
    return {**res, "picked": pick["title"], "said_which": True, "next_topic": after[0]["title"] if after else "",
            "mapped_now": mapped_now, "source": src["title"],
            "note": "say which topic you picked; if he says no, the next one, call teach start with topic set to next_topic"}
