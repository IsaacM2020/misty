"""Live transcript v2. While an online class runs, Phonon-2 on the Neural Engine stays the instant preview (the app's
ears, unchanged). Every 45-90 s of finished audio, Phonon-2.5 English (the after-class model) reads that stretch of
each track again, and its lines replace the preview lines on the same timeline, labels kept (the Mac's sound is the
teacher, the mic is Isaac). The full after-class pass at the end still runs as before.

Why the engine reads `.pcm` files and not the m4a: an m4a can't be opened until it is closed (its index is written on
close), so while the class runs the app also writes each track as plain 16 kHz mono 16-bit samples
(`class.pcm`, `isaac.pcm`) with `<stem>.start` holding the unix time of sample 0. They are scratch: the after-class pass
removes them once the m4a files have been read.

Gentle on the Mac: one chunk at a time (a round that finds another running does nothing), the model runs as a
background-priority child (`taskpolicy -b`), at most 90 s of audio per round, both tracks in one model run, and it stops trying after
three failed reads. Free and local: no cloud.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import numpy as np

from . import class_audio, classes
from .logs import log_event

SR = 16000
MIN_S = 45.0          # a chunk is never shorter: loading the model costs ~14 s a run whatever the length, so rounds stay rare
MAX_S = 90.0          # ...nor longer: one round stays short, and a class that was behind catches up in steps
GAP_S = 3.0           # silence between the teacher's chunk and the mic's in the one model run
HOLD_S = 6.0          # the newest seconds are not read yet: the preview lines for them may still be arriving
CUT_SEARCH_S = 8.0    # the chunk ends in the quietest moment of its last 8 s, so a word is never cut in half
CUT_WIN_S = 0.3
MAX_FAILS = 3
LIVE_TIMEOUT_S = 180
STEMS = {"class": "mac", "isaac": "room"}          # file stem -> class_segments.source

_busy: set[str] = set()                            # sessions with a round running
_fails: dict[str, int] = {}


# ---- pure pieces (tested without a microphone) ----------------------------------------------------------------------

def plan_window(avail_s: float, done_s: float, *, min_s: float = MIN_S, max_s: float = MAX_S,
                hold_s: float = HOLD_S) -> tuple[float, float] | None:
    """The next stretch to read, in class seconds: from where the last round stopped to at most a minute on, never
    into the newest `hold_s` seconds. None until at least `min_s` has finished."""
    end = min(done_s + max_s, avail_s - hold_s)
    if end - done_s < min_s:
        return None
    return done_s, end


def find_cut(x: np.ndarray, start_s: float, lo_s: float, hi_s: float, win_s: float = CUT_WIN_S) -> float:
    """Class time of the quietest `win_s` window between lo_s and hi_s in `x` (samples that begin at `start_s`).
    The middle of that window, so the cut falls in a pause."""
    i0 = max(0, int(round((lo_s - start_s) * SR)))
    i1 = min(len(x), int(round((hi_s - start_s) * SR)))
    w = max(1, int(win_s * SR))
    if i1 - i0 <= w:
        return min(hi_s, start_s + len(x) / SR)
    seg = np.abs(x[i0:i1].astype(np.float32))
    csum = np.concatenate(([0.0], np.cumsum(seg, dtype=np.float64)))
    energy = csum[w:] - csum[:-w]
    k = int(np.argmin(energy))                       # first quietest window: flat audio cuts at the early end
    return start_s + (i0 + k + w // 2) / SR


def in_window(segs: list[dict], source: str, w0: float, w1: float) -> list[dict]:
    """This track's lines whose middle lies in [w0, w1): a line that straddles a cut belongs to exactly one window."""
    return [g for g in segs if g["source"] == source and w0 <= (g["t_start"] + g["t_end"]) / 2 < w1]


def join_tracks(parts: list[tuple[str, np.ndarray]]) -> tuple[np.ndarray, list[tuple[str, float, float]]]:
    """Several tracks as one array, `GAP_S` of silence apart, and where each one sits: [(stem, from_s, to_s)]."""
    gap = np.zeros(int(GAP_S * SR), np.float32)
    pieces, spans, at = [], [], 0.0
    for i, (stem, x) in enumerate(parts):
        if i:
            pieces.append(gap)
            at += GAP_S
        pieces.append(x)
        spans.append((stem, at, at + len(x) / SR))
        at += len(x) / SR
    return np.concatenate(pieces), spans


def split_lines(segs: list[dict], spans: list[tuple[str, float, float]]) -> dict[str, list[dict]]:
    """The model's lines for the joined array handed back to their own track, each track's time starting at 0 again.
    A line whose middle is in the gap between tracks belongs to nobody and is dropped, and a line that runs across the
    gap is cut back to its own track."""
    out: dict[str, list[dict]] = {stem: [] for stem, _a, _b in spans}
    for g in segs:
        mid = (g["start"] + g["end"]) / 2
        for stem, a, b in spans:
            if a <= mid < b:        # clamped: a line the model ran across the gap must not start before 0 or end past the track
                out[stem].append({**g, "start": max(0.0, g["start"] - a), "end": min(b - a, g["end"] - a)})
                break
    return out


# ---- the database swap ----------------------------------------------------------------------------------------------

def replace_window(con, sid: str, source: str, w0: float, w1: float, lines: list[dict],
                   keep_dir: Path | None = None) -> dict:
    """Swap this track's preview lines in [w0, w1) for the corrected `lines` ({t_start, t_end, text}, class time).
    Nothing read means nothing changes: an empty answer never wipes preview lines that were really heard. The preview
    that was replaced is appended to `live-preview.jsonl` in `keep_dir` for comparing."""
    from .scrub import scrub_text
    rows = [dict(r) for r in con.execute("SELECT id, t_start, t_end, source, text FROM class_segments "
                                         "WHERE session_id=? AND source=? ORDER BY t_start, id", (sid, source))]
    old = in_window(rows, source, w0, w1)
    clean = []
    for ln in lines:
        text = scrub_text(" ".join(ln["text"].split()), "message").text
        if text:
            clean.append((float(ln["t_start"]), max(float(ln["t_start"]), float(ln["t_end"])), text))
    if not clean:
        return {"removed": [], "added": []}
    from .db import tx
    added = []
    with tx(con):
        for g in old:
            con.execute("DELETE FROM class_segments WHERE id=?", (g["id"],))          # fires the FTS delete trigger
        for a, b, text in clean:
            cur = con.execute("INSERT INTO class_segments(session_id, t_start, t_end, source, text) VALUES (?,?,?,?,?)",
                              (sid, a, b, source, text))
            added.append({"id": cur.lastrowid, "t_start": a, "t_end": b, "source": source, "text": text})
    if keep_dir is not None and old:
        try:
            with (keep_dir / "live-preview.jsonl").open("a", encoding="utf-8") as f:
                for g in old:
                    f.write(json.dumps({k: g[k] for k in ("t_start", "t_end", "source", "text")}) + "\n")
        except OSError:
            pass
    return {"removed": [g["id"] for g in old], "added": added}


# ---- reading the scratch tracks -------------------------------------------------------------------------------------

def _track(d: Path, stem: str) -> tuple[float, int] | None:
    """(unix start, samples written so far) of a live track, or None when it isn't there yet."""
    try:
        start = float((d / f"{stem}.start").read_text().strip())
        return start, (d / f"{stem}.pcm").stat().st_size // 2
    except (OSError, ValueError):
        return None


def _read(d: Path, stem: str, start_unix: float, session_start: float, w0: float, w1: float) -> np.ndarray:
    """The track's samples for class seconds [w0, w1) as float32, zero where the track had not started."""
    n = int(round((w1 - w0) * SR))
    first = int(round((w0 - (start_unix - session_start)) * SR))     # index in the file of the window's first sample
    out = np.zeros(n, np.float32)
    lo = max(first, 0)
    skip = lo - first
    want = n - skip
    if want <= 0:
        return out
    with (d / f"{stem}.pcm").open("rb") as f:
        f.seek(lo * 2)
        raw = f.read(want * 2)
    got = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    out[skip: skip + len(got)] = got
    return out


LOW_PRIORITY = (["/usr/sbin/taskpolicy", "-b"], ["/usr/bin/nice", "-n", "19"])     # background QoS, else plain nice


def low_priority() -> list[str]:
    """The command prefix that makes the model a background-priority child (the first one this Mac has)."""
    for cmd in LOW_PRIORITY:
        if Path(cmd[0]).exists():
            return list(cmd)
    return []


def _live_stt(samples: np.ndarray) -> list[dict] | None:
    """Phonon-2.5 English on one chunk, at background priority. None: not set up, or the run failed."""
    cmd = class_audio._fermion()
    if cmd is None:
        return None
    return class_audio._phonon(samples, [*low_priority(), *cmd], timeout=LIVE_TIMEOUT_S)


# ---- one round ------------------------------------------------------------------------------------------------------

def _state(d: Path) -> dict:
    try:
        return json.loads((d / "live-correct.json").read_text())
    except (OSError, ValueError):
        return {}


def _empty(up_to: float, **extra) -> dict:
    return {"changes": [], "up_to": up_to, **extra}


async def correct_round(ctx, sid: str, stt=None) -> dict:
    """One round for one live class: read the next finished stretch of both tracks with Phonon-2.5 and swap the lines.
    {"changes": [{"removed": [ids], "added": [segments]}], "up_to": class seconds corrected so far}."""
    if sid in _busy:
        return _empty(0, busy=True)
    _busy.add(sid)                                    # taken before the first await: a second caller sees it at once
    try:
        return await _round(ctx, sid, stt or _live_stt)
    finally:
        _busy.discard(sid)


async def _round(ctx, sid: str, stt) -> dict:
    con = ctx.con
    home = ctx.paths.home
    d = class_audio.audio_dir(home, sid)
    st = _state(d)
    done = float(st.get("done", 0.0))
    try:
        got = classes.get(con, sid)
    except Exception:                                   # no such class
        return _empty(done)
    sess = got["session"]
    if sess["status"] != "live" or sess.get("mode") != "online" or _fails.get(sid, 0) >= MAX_FAILS:
        return _empty(done)
    t0 = float(sess["started_at"])
    tracks = {stem: info for stem in STEMS if (info := _track(d, stem)) is not None}
    if not tracks:
        return _empty(done)
    # how much class time both tracks cover: the shorter one decides, so both are read over the same window
    avail = min((start - t0) + n / SR for start, n in tracks.values())
    plan = plan_window(avail, done)
    if plan is None:
        return _empty(done)
    w0, w1 = plan
    began = time.monotonic()
    mix = None
    try:
        for stem, (start, _n) in tracks.items():          # find a pause near the end to cut in
            x = np.abs(_read(d, stem, start, t0, w0, w1))
            mix = x if mix is None else mix + x
    except OSError:
        return _empty(done)
    if w1 - w0 > MIN_S:
        w1 = find_cut(mix, w0, max(w0 + MIN_S, w1 - CUT_SEARCH_S), w1)
    n = int(round((w1 - w0) * SR))
    try:
        audio = {stem: _read(d, stem, start, t0, w0, w1)[:n] for stem, (start, _) in tracks.items()}
    except OSError:                                    # the class ended and the scratch went while we were reading
        return _empty(done)
    parts: list[tuple[str, np.ndarray]] = []
    for stem in ("class", "isaac"):                    # teacher first: the mic is checked against what she said
        if stem not in audio:
            continue
        x = audio[stem]
        if stem == "isaac" and "class" in audio:
            try:                                       # speakers on: take the class out of the mic first
                from . import echo_cancel
                x, _info = echo_cancel.suppress(x, audio["class"], 0)
            except Exception:
                pass
        if float(np.abs(x).max(initial=0)) >= 1e-4:    # digital silence: nothing to read, nothing to replace
            parts.append((stem, x))
    lines: dict[str, list[dict]] = {}
    if parts:
        # loading the model takes ~14 s whatever the audio length, so both tracks go through ONE run, a gap apart
        big, spans = join_tracks(parts)
        got_lines = await asyncio.to_thread(stt, big)
        if got_lines is None:
            _fails[sid] = _fails.get(sid, 0) + 1
            return _empty(done)                         # cursor stays: the same stretch is tried again next round
        lines = split_lines(got_lines, spans)
    _fails.pop(sid, None)

    from . import subject_words
    changes = []
    for stem in ("class", "isaac"):
        if stem not in lines:
            continue
        source = STEMS[stem]
        shifted = [{"t_start": w0 + s["start"], "t_end": w0 + s["end"], "text": s["text"]} for s in lines[stem]]
        if source == "room":
            near = [w for g in classes.get(con, sid)["segments"] if g["source"] == "mac"
                    and g["t_end"] >= w0 - class_audio.ECHO_WINDOW_S and g["t_start"] <= w1 + 1
                    for w in class_audio.words(g["text"])]
            shifted = [s for s in shifted if not class_audio.is_teacher_echo(s["text"], near)]
        for s in shifted:
            s["text"] = subject_words.apply_dictionary(home, s["text"])
        ch = replace_window(con, sid, source, w0, w1, shifted, keep_dir=d)
        if ch["removed"] or ch["added"]:
            changes.append(ch)
    try:
        (d / "live-correct.json").write_text(json.dumps({"done": w1, "at": time.time()}))
    except OSError:
        pass
    log_event("CLASS_LIVE", item_id=sid, count=sum(len(c["added"]) for c in changes),
              n=sum(len(c["removed"]) for c in changes), ms=int((time.monotonic() - began) * 1000))
    return {"changes": changes, "up_to": w1}
