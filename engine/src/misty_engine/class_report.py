"""`misty class-audio-report <session>`: what an online class left on disk, in counts only.

Read-only. It opens the database read-only, only looks at the class folder, and prints numbers: never a word of the
transcript. For each track, the seconds recorded (`tracks.json`); for the saved transcript, segments and words per
source (`mac` is the teacher's track, `room` is the mic); and what the after-class pass kept of the live preview.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import class_audio

_SID = re.compile(r"[A-Za-z0-9_-]{1,40}")
LABELS = {"class": "class.m4a  (Mac sound, teacher)", "isaac": "isaac.m4a  (mic)"}


def _mmss(s: float) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def _tracks(d: Path) -> list[str]:
    try:
        meta = json.loads((d / "tracks.json").read_text())
    except (OSError, ValueError):
        return ["  tracks.json: missing or unreadable"]
    out = []
    for stem, label in LABELS.items():
        m = meta.get(stem) if isinstance(meta, dict) else None
        if not isinstance(m, dict) or not isinstance(m.get("seconds"), (int, float)):
            out.append(f"  {label}: not recorded")
            continue
        out.append(f"  {label}: {m['seconds']:.1f} s ({_mmss(m['seconds'])})" + ("" if (d / f"{stem}.m4a").exists()
                                                                                   else "  [file missing]"))
    return out


def _counts(rows) -> dict[str, tuple[int, int]]:
    """{source: (segments, words)}; the text is counted here and goes no further."""
    out: dict[str, tuple[int, int]] = {}
    for source, text in rows:
        n, w = out.get(source, (0, 0))
        out[source] = (n + 1, w + len((text or "").split()))
    return out


def _lines(counts: dict[str, tuple[int, int]]) -> list[str]:
    rows = [f"  {src:<5} {counts.get(src, (0, 0))[0]:>5} segments {counts.get(src, (0, 0))[1]:>7} words"
            for src in ("mac", "room")]
    rows += [f"  {src:<5} {n:>5} segments {w:>7} words" for src, (n, w) in sorted(counts.items()) if src not in ("mac", "room")]
    return rows


def _saved(db: Path, sid: str) -> dict[str, tuple[int, int]] | None:
    """The saved transcript's counts, or None when the database or the class isn't there. Opened read-only."""
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            con.execute("PRAGMA query_only=ON")
            if con.execute("SELECT 1 FROM class_sessions WHERE id=?", (sid,)).fetchone() is None:
                return None
            return _counts(con.execute("SELECT source, text FROM class_segments WHERE session_id=?", (sid,)))
        finally:
            con.close()
    except sqlite3.Error:
        return None


def report(home: Path, sid: str) -> str | None:
    """The report as text, or None when there is neither a class folder nor a saved class for this id."""
    if not _SID.fullmatch(sid):
        return None
    d = class_audio.audio_dir(home, sid)
    saved = _saved(home / "misty.db", sid)
    if not d.is_dir() and saved is None:
        return None
    out = [f"class-audio-report {sid}", "", "recorded tracks (tracks.json):"]
    out += _tracks(d) if d.is_dir() else ["  no audio folder"]
    out += ["", "saved transcript (database):"]
    out += _lines(saved) if saved is not None else ["  none saved"]
    out += ["", "after-class pass:"]
    try:
        preview = json.loads((d / "preview.json").read_text())
    except (OSError, ValueError):
        preview = None
    if not isinstance(preview, list):
        out.append("  has not run (no preview.json): the transcript above is the live preview")
    else:
        out.append("  has run: the live preview was replaced by the audio transcript")
        out += ["  live preview that was replaced:"] + [
            "  " + r for r in _lines(_counts((g.get("source"), g.get("text")) for g in preview if isinstance(g, dict)))]
        out.append("  teacher echo dropped from the mic: not recorded (preview.json holds the live preview, "
                   "not what the pass dropped)")
    return "\n".join(out)
