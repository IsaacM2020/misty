"""Class sessions: the transcript Misty's ears hear in a class (text only, the audio is never kept), Isaac's typed
notes and the gaps when the lid was closed. One session is live at a time. A session nobody closed (the app quit
mid-class) is ended after 30 quiet minutes, and transcripts are deleted 6 months after the class.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime

from .clock import SGT
from .db import tx
from .errors import Fatal
from .scrub import scrub_text

STALE_S = 30 * 60
KEEP_S = 183 * 86400          # transcripts: 6 months (Isaac, 2026-10-23); notes stay forever
OPEN = ("live", "paused")


def _title(subject: str, ts: float) -> str:
    d = datetime.fromtimestamp(ts, SGT)
    return f"{subject}, {d.day} {d.strftime('%b')}"


def is_default_title(sess: dict) -> bool:
    """True while the class still has the name it was born with ("Chem, 14 Nov"), so a better one may replace it."""
    return sess["title"] == _title(sess["subject"], sess["started_at"])


def dated(name: str, ts: float) -> str:
    """A calendar event or topic name with the class day on the end: "Projectile motion, 14 Nov"."""
    d = datetime.fromtimestamp(ts, SGT)
    return f"{' '.join(name.split())[:60].rstrip(' ,.')}, {d.day} {d.strftime('%b')}"


def rename(con, sid: str, title: str | None, subject: str | None) -> dict:
    """His own name or subject for a class. Whatever he typed stays: only a default name is ever replaced by a model."""
    _session(con, sid)
    with tx(con):
        if title and title.strip():
            con.execute("UPDATE class_sessions SET title=? WHERE id=?", (" ".join(title.split())[:80], sid))
        if subject and subject.strip():
            con.execute("UPDATE class_sessions SET subject=? WHERE id=?", (subject.strip()[:40], sid))
    return _out(_session(con, sid))


def _session(con, sid: str):
    r = con.execute("SELECT * FROM class_sessions WHERE id=?", (sid,)).fetchone()
    if r is None:
        raise Fatal("NO_CLASS")
    return r


def _out(r) -> dict:
    d = dict(r)
    d["gaps"] = json.loads(d["gaps"] or "[]")
    d["notes"] = json.loads(d.pop("notes_json")) if d.get("notes_json") else None
    return d


MODES = ("room", "online")


def start(con, clock, subject: str, title: str | None, cal_event_id: str | None, mode: str = "room") -> dict:
    if mode not in MODES:
        raise Fatal("BAD_PARAMS")
    now = clock.now()
    sid = "cs_" + secrets.token_hex(4)
    with tx(con):
        con.execute("UPDATE class_sessions SET status='ended', ended_at=? WHERE status IN ('live','paused')", (now,))
        con.execute("INSERT INTO class_sessions(id, subject, title, cal_event_id, started_at, last_heard_at, mode) "
                    "VALUES (?,?,?,?,?,?,?)", (sid, subject, title or _title(subject, now), cal_event_id, now, now, mode))
    return _out(_session(con, sid))


def add_segment(con, sid: str, t_start: float, t_end: float, source: str, text: str) -> int | None:
    text = scrub_text(" ".join(text.split()), "message").text
    if not text:
        return None
    _session(con, sid)
    with tx(con):
        cur = con.execute("INSERT INTO class_segments(session_id, t_start, t_end, source, text) VALUES (?,?,?,?,?)",
                          (sid, t_start, t_end, source, text))
        con.execute("UPDATE class_sessions SET last_heard_at=started_at+? WHERE id=?", (t_end, sid))
    return cur.lastrowid


def replace_segments(con, sid: str, segs: list[dict]) -> list[dict]:
    """Swap the live preview for the after-class transcript. Returns the preview that was there (for comparing)."""
    _session(con, sid)
    old = [dict(r) for r in con.execute("SELECT t_start, t_end, source, text FROM class_segments WHERE session_id=? "
                                         "ORDER BY t_start, id", (sid,))]
    rows = [(sid, s["t_start"], s["t_end"], s["source"], scrub_text(" ".join(s["text"].split()), "message").text)
            for s in segs]
    with tx(con):
        con.execute("DELETE FROM class_segments WHERE session_id=?", (sid,))
        con.executemany("INSERT INTO class_segments(session_id, t_start, t_end, source, text) VALUES (?,?,?,?,?)",
                        [r for r in rows if r[4]])
    return old


MAX_SLIDES = 120


def add_slide(con, sid: str, t: float, text: str) -> int | None:
    """Text read off the shared screen (on this Mac). Repeats and runaway classes are dropped."""
    text = scrub_text(" ".join(text.split()), "message").text[:1200]
    if len(text.split()) < 3:
        return None
    _session(con, sid)
    last = con.execute("SELECT text FROM class_slides WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    n = con.execute("SELECT COUNT(*) FROM class_slides WHERE session_id=?", (sid,)).fetchone()[0]
    if (last and last["text"] == text) or n >= MAX_SLIDES:
        return None
    with tx(con):
        cur = con.execute("INSERT INTO class_slides(session_id, t, text) VALUES (?,?,?)", (sid, t, text))
    return cur.lastrowid


def save_notes(con, sid: str, text: str) -> None:
    _session(con, sid)
    con.execute("UPDATE class_sessions SET his_notes=? WHERE id=?", (scrub_text(text, "message").text, sid))


def pause(con, clock, sid: str, reason: str) -> None:
    r = _session(con, sid)
    if r["status"] != "live":
        return
    gaps = json.loads(r["gaps"]) + [{"from": clock.now() - r["started_at"], "to": None, "reason": reason}]
    con.execute("UPDATE class_sessions SET status='paused', gaps=? WHERE id=?", (json.dumps(gaps), sid))


def resume(con, clock, sid: str) -> None:
    r = _session(con, sid)
    if r["status"] != "paused":
        return
    gaps = json.loads(r["gaps"])
    if gaps and gaps[-1]["to"] is None:
        gaps[-1]["to"] = clock.now() - r["started_at"]
    con.execute("UPDATE class_sessions SET status='live', gaps=?, last_heard_at=? WHERE id=?",
                (json.dumps(gaps), clock.now(), sid))


def end(con, clock, sid: str) -> None:
    r = _session(con, sid)
    if r["status"] not in OPEN:
        return
    gaps = json.loads(r["gaps"])
    if gaps and gaps[-1]["to"] is None:
        gaps[-1]["to"] = clock.now() - r["started_at"]
    con.execute("UPDATE class_sessions SET status='ended', ended_at=?, gaps=? WHERE id=?",
                (clock.now(), json.dumps(gaps), sid))


def get(con, sid: str) -> dict:
    s = _out(_session(con, sid))
    segs = [dict(r) for r in con.execute("SELECT id, t_start, t_end, source, text FROM class_segments "
                                          "WHERE session_id=? ORDER BY t_start, id", (sid,))]
    acts = [dict(r) for r in con.execute("SELECT * FROM class_actions WHERE session_id=? ORDER BY rowid", (sid,))]
    slides = [dict(r) for r in con.execute("SELECT id, t, text FROM class_slides WHERE session_id=? ORDER BY t, id", (sid,))]
    return {"session": s, "segments": segs, "actions": acts, "slides": slides}


def list(con, subject: str | None = None, limit: int = 50) -> list[dict]:  # noqa: A001 (module API name)
    q, args = "SELECT * FROM class_sessions WHERE merged_into IS NULL", []      # a merged-away half is hidden
    if subject:
        q, args = q + " AND subject=?", [subject]
    rows = con.execute(q + " ORDER BY started_at DESC LIMIT ?", (*args, limit)).fetchall()
    return [_out(r) for r in rows]


_TIMER = re.compile(r"^(\s*)\[(\d{1,3}):(\d{2})\]")


def _shift_timers(text: str, by: float) -> str:
    """His typed lines start with the class time ("[05:12] ..."). Moved into a longer class they move by `by` seconds."""
    def one(m):
        t = int(m[2]) * 60 + int(m[3]) + int(by)
        return f"{m[1]}[{t // 60:02d}:{t % 60:02d}]"
    return "\n".join(_TIMER.sub(one, ln) for ln in text.split("\n"))


def _fold(con, kid: str, gid: str) -> float:
    """Move class `gid` onto class `kid` (the earlier one). The caller holds the transaction. Returns the shift."""
    keep, gone = _session(con, kid), _session(con, gid)
    for r in (keep, gone):
        if r["status"] in OPEN or r["merged_into"]:
            raise Fatal("CLASS_NOT_MERGEABLE")
    off = gone["started_at"] - keep["started_at"]
    gaps = json.loads(keep["gaps"] or "[]")
    first_end = (keep["ended_at"] or keep["last_heard_at"] or keep["started_at"]) - keep["started_at"]
    if off > first_end:
        gaps.append({"from": first_end, "to": off, "reason": "restart"})
    gaps += [{"from": g["from"] + off, "to": None if g["to"] is None else g["to"] + off, "reason": g["reason"]}
             for g in json.loads(gone["gaps"] or "[]")]
    his = "\n".join(x for x in (keep["his_notes"].strip("\n"), _shift_timers(gone["his_notes"], off).strip("\n")) if x)
    ends = [x for x in (keep["ended_at"], gone["ended_at"]) if x is not None]
    heard = [x for x in (keep["last_heard_at"], gone["last_heard_at"]) if x is not None]
    con.execute("UPDATE class_segments SET session_id=?, t_start=t_start+?, t_end=t_end+? WHERE session_id=?",
                (kid, off, off, gid))                      # same row ids, so every segment id in the old notes still holds
    con.execute("UPDATE class_slides SET session_id=?, t=t+? WHERE session_id=?", (kid, off, gid))
    con.execute("UPDATE class_actions SET session_id=? WHERE session_id=?", (kid, gid))
    con.execute("UPDATE class_sessions SET status='ended', ended_at=?, last_heard_at=?, gaps=?, his_notes=?, cost=? "
                "WHERE id=?", (max(ends) if ends else None, max(heard) if heard else None, json.dumps(gaps), his,
                               (keep["cost"] or 0) + (gone["cost"] or 0), kid))
    con.execute("UPDATE class_sessions SET merged_into=? WHERE id=?", (kid, gid))
    return off


def merge_many(con, ids: list[str], title: str | None = None) -> dict:
    """Make one class out of two or more halves (one lesson recorded in parts, or a duplicate). The class that started
    first is kept (its title, subject and date stay unless `title` renames it); every other one is absorbed in time
    order: transcript, slides, action items, typed notes and gaps move onto the kept timeline, shifted by the real
    difference between the start times, so the breaks show as gaps. One transaction: any failure changes nothing.
    Nothing is deleted: an absorbed class stays in the table with merged_into set (the list hides it) and its audio
    stays where it is, as ordered parts (absorbed.started_at - kept.started_at is the shift). The merged class goes
    back to 'ended' because its notes were made from part of the lesson and must be made again."""
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise Fatal("BAD_PARAMS")
    rows = sorted((_session(con, i) for i in ids), key=lambda r: (r["started_at"], r["id"]))
    kid = rows[0]["id"]
    offs = {}
    with tx(con):
        for r in rows[1:]:
            offs[r["id"]] = _fold(con, kid, r["id"])
        if title and title.strip():
            con.execute("UPDATE class_sessions SET title=? WHERE id=?", (" ".join(title.split())[:80], kid))
    gone = [r["id"] for r in rows[1:]]
    return {"kept": kid, "absorbed": gone[0] if len(gone) == 1 else gone, "absorbed_all": gone,
            "offset": offs[gone[0]], "offsets": offs}


def merge(con, a: str, b: str) -> dict:
    """Two halves of one lesson (see merge_many)."""
    if a == b:
        raise Fatal("BAD_PARAMS")
    return merge_many(con, [a, b])


def trash_audio(home, sid: str, trash=None) -> bool:
    """Move a class's audio folder to the Trash (never a hard delete). True if there was one."""
    import shutil
    from pathlib import Path
    src = Path(home) / "class-audio" / sid
    if not src.exists():
        return False
    dest_dir = Path(trash) if trash is not None else Path.home() / ".Trash"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"Misty class {sid}"
    n = 1
    while dest.exists():
        n += 1
        dest = dest_dir / f"Misty class {sid} {n}"
    shutil.move(str(src), str(dest))
    return True


def delete_many(con, ids: list[str], home=None, trash=None) -> list[str]:
    """Delete classes and everything tied to them: segments (and their FTS rows), slides, action items, teacher
    sources and class evidence, the classes folded into them (merged parts) and their audio, which goes to the Trash.
    One transaction for the db; the audio moves after it commits. Returns every class id removed."""
    for i in ids:
        _session(con, i)
    gone, todo = [], [*dict.fromkeys(ids)]
    while todo:
        cur = todo.pop()
        gone.append(cur)
        todo += [r["id"] for r in con.execute("SELECT id FROM class_sessions WHERE merged_into=?", (cur,))]
    with tx(con):
        for sid in gone:
            con.execute("DELETE FROM class_segments WHERE session_id=?", (sid,))   # fires the FTS delete trigger
            con.execute("DELETE FROM class_slides WHERE session_id=?", (sid,))
            con.execute("DELETE FROM class_actions WHERE session_id=?", (sid,))
            for r in con.execute("SELECT id FROM teach_sources WHERE kind='class' AND (class_id=? OR locator=?)",
                                 (sid, sid)).fetchall():
                con.execute("DELETE FROM teach_chunks WHERE source_id=?", (r["id"],))
                con.execute("DELETE FROM teach_sources WHERE id=?", (r["id"],))
            con.execute("DELETE FROM teach_evidence WHERE origin='class' AND origin_id LIKE ? ESCAPE '\\'",
                        (sid.replace("_", "\\_").replace("%", "\\%") + ":%",))
            con.execute("DELETE FROM class_sessions WHERE id=?", (sid,))
    if home is not None:
        for sid in gone:
            trash_audio(home, sid, trash)
    return gone


def delete(con, sid: str, home=None, trash=None) -> list[str]:
    return delete_many(con, [sid], home, trash)


def close_stale(con, clock) -> list[str]:
    """Sessions left open by an app that quit mid-class: nothing heard for 30 minutes means the class is over."""
    rows = con.execute("SELECT id FROM class_sessions WHERE status IN ('live','paused') AND last_heard_at < ?",
                       (clock.now() - STALE_S,)).fetchall()
    for r in rows:
        end(con, clock, r["id"])
    return [r["id"] for r in rows]


def needs_notes(con) -> list[str]:
    """Ended classes whose notes never got made (the engine stopped first)."""
    return [r["id"] for r in con.execute("SELECT id FROM class_sessions WHERE status IN ('ended','noting')")]


def prune(con, clock) -> int:
    """Delete transcripts of classes more than 6 months old. The notes and action items stay."""
    rows = con.execute("SELECT id FROM class_sessions WHERE pruned=0 AND started_at < ?",
                       (clock.now() - KEEP_S,)).fetchall()
    with tx(con):
        for r in rows:
            con.execute("DELETE FROM class_segments WHERE session_id=?", (r["id"],))
            con.execute("DELETE FROM class_slides WHERE session_id=?", (r["id"],))
            con.execute("UPDATE class_sessions SET pruned=1 WHERE id=?", (r["id"],))
    return len(rows)
