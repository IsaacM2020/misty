"""Ask the class: find the transcript segments that match a question (full-text search, plus the segment either side
for context) and have the model answer from those only, naming the segments it used. During a class it searches what
has been said so far. A class older than 6 months has no transcript left, so it answers from that class's notes.
"""
from __future__ import annotations

import re
from datetime import date

from . import classes, ways
from .openrouter import user_approved

NOT_FOUND = "I couldn't find that in the class."
GONE = "The transcript for this class is gone (kept 6 months)."

SYSTEM = """Isaac asks about his classes. Answer from the class material below only, in 1-4 short plain sentences.
If it isn't there, say you couldn't find it. Reply with JSON only: {"answer": "...", "cites": [segment ids used]}"""


def _match(question: str) -> str | None:
    words = {w for w in re.findall(r"\w+", question.lower()) if len(w) > 2}
    return " OR ".join(f'"{w}"' for w in sorted(words)) or None


def retrieve(con, question: str, *, sid: str | None = None, subject: str | None = None, since: str | None = None,
             k: int = 8) -> list[dict]:
    q = _match(question)
    if q is None:
        return []
    sql = ("SELECT g.id, g.session_id, g.t_start FROM class_segments_fts f JOIN class_segments g ON g.id=f.rowid "
           "JOIN class_sessions s ON s.id=g.session_id WHERE class_segments_fts MATCH ?")
    args: list = [q]
    if sid:
        sql, args = sql + " AND g.session_id=?", args + [sid]
    if subject:
        sql, args = sql + " AND s.subject=?", args + [subject]
    if since:
        sql, args = sql + " AND date(s.started_at, 'unixepoch', '+8 hours') >= ?", args + [since]
    hits = con.execute(sql + " ORDER BY bm25(class_segments_fts) LIMIT ?", (*args, k)).fetchall()
    out: dict[int, dict] = {}
    for h in hits:
        near = con.execute("SELECT id, session_id, t_start, text FROM class_segments WHERE session_id=? "
                           "ORDER BY t_start, id", (h["session_id"],)).fetchall()
        ids = [r["id"] for r in near]
        i = ids.index(h["id"])
        for r in near[max(0, i - 1):i + 2]:
            out[r["id"]] = {"session": r["session_id"], "seg": r["id"], "t_start": r["t_start"], "text": r["text"]}
    return sorted(out.values(), key=lambda g: (g["session"], g["t_start"], g["seg"]))


def _notes_text(sess: dict) -> str:
    n = sess.get("notes") or {}
    lines = [n.get("summary", "")]
    for sec in n.get("sections", []):
        lines += [f"{sec['heading']}:"] + [f"- {p['text']}" for p in sec["points"]]
    lines += [f"- {r['text']}" for r in n.get("remember", [])]
    return "\n".join(x for x in lines if x)


async def _run(ctx, parts: list[str]) -> dict:
    with user_approved():                            # he asked this himself
        res = await ctx.runner.run(ways.with_style(ctx.con, SYSTEM), parts, "class_ask", purpose="class", model_key="haiku", max_tokens=500)
    return res.json or {}


async def ask(ctx, question: str, *, sid: str | None = None, subject: str | None = None,
              since: str | None = None) -> dict:
    con = ctx.con
    if sid:
        sess = classes.get(con, sid)["session"]
        if sess["pruned"]:
            if not sess.get("notes"):
                return {"answer": GONE, "cites": []}
            r = await _run(ctx, [f"Question: {question}", f"Notes of {sess['title']}:\n" + _notes_text(sess)])
            return {"answer": f"{GONE} {str(r.get('answer') or '').strip()}".strip(), "cites": []}
    found = retrieve(con, question, sid=sid, subject=subject, since=since)
    if not found:
        return {"answer": NOT_FOUND, "cites": []}
    titles = {g["session"]: classes.get(con, g["session"])["session"]["title"] for g in found}
    material = "\n".join(f"[{g['seg']}] ({titles[g['session']]}, {int(g['t_start']) // 60:02d}:"
                         f"{int(g['t_start']) % 60:02d}) {g['text']}" for g in found)
    r = await _run(ctx, [f"Question: {question}", "Class material:\n" + material])
    by_id = {g["seg"]: g for g in found}
    cites = [{"session": by_id[c]["session"], "seg": c, "t_start": by_id[c]["t_start"]}
             for c in (r.get("cites") or []) if isinstance(c, int) and c in by_id]
    return {"answer": str(r.get("answer") or NOT_FOUND).strip(), "cites": cites}


def notes_for_chat(con, which: str, subject: str | None = None, today: date | None = None) -> dict:
    """One class's notes as plain text for the chat model: 'last' (optionally of a subject) or a session id.
    `today` set: each due date says how far off it is, since the model reads "due 2026-11-05" as still ahead."""
    if which == "last":
        # A recording that caught almost nothing ('too_little') isn't "the last class": skip to the one with notes.
        rows = [r for r in classes.list(con, subject, limit=10) if r.get("status") != "too_little"]
        if not rows:
            return {"ok": False, "error": "no classes yet" + (f" in {subject}" if subject else "")}
        sid = rows[0]["id"]
    else:
        sid = which
    got = classes.get(con, sid)
    s = got["session"]
    if not s.get("notes"):
        return {"ok": True, "class": s["title"], "status": s["status"],
                "transcript_start": " ".join(g["text"] for g in got["segments"])[:1500]}
    acts = [f"- {a['text']}" + (f" (due {_when(a['due'], today)})" if a["due"] else "") for a in got["actions"]]
    n = s["notes"]
    # The debrief's "where you got lost" and "next step" (step 7). Always present: a missing key left the model free
    # to make one up (6 Nov Spanish had no next step and the spoken debrief invented one), so say "none" outright.
    # Short parts first, the long notes last: chat caps a tool result, and a cut must never land on these.
    return {"ok": True, "class": s["title"],
            "where_he_got_lost": "\n".join(f"- {x['text']}" for x in n.get("lost") or []) or NONE,
            "next_step": (n.get("next_step") or {}).get("text") or NONE,
            "action_items": "\n".join(acts) or "none", "notes": _notes_text(s)}


NONE = "none in the notes: skip this part, don't make one up"


def _when(due: str, today: date | None) -> str:
    """'2026-11-05' as 'Thu 5 Nov, 2 days ago' / 'today' / 'tomorrow' / 'in 3 days' (just the date without today)."""
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return due
    day = f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
    if today is None:
        return day
    k = (d - today).days
    rel = {0: "today", 1: "tomorrow", -1: "yesterday, already past"}.get(
        k, f"in {k} days" if k > 0 else f"{-k} days ago, already past")
    return f"{day}, {rel}"


