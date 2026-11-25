"""Live transcript v2: Phonon-2 stays the instant preview, and every 30-60 s of finished audio Phonon-2.5 English reads
that stretch again and the corrected lines replace the preview on the same timeline (labels from the track). One chunk
at a time, never a round while one is running, never wipes good preview lines with nothing, free and local."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from misty_engine import class_live, classes
from misty_engine.paths import MistyPaths

SR = 16000


# ---- when to read a chunk -------------------------------------------------------------------------------------------

def test_no_chunk_until_forty_five_seconds_are_finished():
    assert class_live.plan_window(avail_s=20, done_s=0) is None
    assert class_live.plan_window(avail_s=50, done_s=0) is None            # 50 - 6 s held back = 44 s, not enough yet
    assert class_live.plan_window(avail_s=52, done_s=0) == (0, 46)


def test_last_seconds_are_held_back_so_a_word_is_never_cut_or_late():
    w0, w1 = class_live.plan_window(avail_s=100, done_s=40)
    assert w0 == 40 and w1 <= 100 - class_live.HOLD_S


def test_a_chunk_is_never_longer_than_ninety_seconds_and_catches_up_in_steps():
    assert class_live.plan_window(avail_s=900, done_s=0) == (0, 90)
    assert class_live.plan_window(avail_s=900, done_s=90) == (90, 180)


def test_cut_lands_in_the_quietest_spot_near_the_end_of_the_chunk():
    t = np.arange(60 * SR) / SR
    x = (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    x[int(55.0 * SR):int(55.4 * SR)] = 0.0                                  # a pause at 55.0-55.4 s
    cut = class_live.find_cut(x, start_s=0.0, lo_s=52.0, hi_s=60.0)
    assert 55.0 <= cut <= 55.4


def test_cut_on_flat_audio_just_uses_the_end():
    x = np.full(60 * SR, 0.1, np.float32)
    assert class_live.find_cut(x, start_s=0.0, lo_s=52.0, hi_s=60.0) >= 52.0


# ---- replacing the preview lines ------------------------------------------------------------------------------------

def _seg(i, a, b, src, text):
    return {"id": i, "t_start": a, "t_end": b, "source": src, "text": text}


def test_only_this_tracks_preview_lines_inside_the_window_are_replaced():
    preview = [_seg(1, 0, 4, "mac", "uh the mole"), _seg(2, 5, 9, "mac", "is a unit"),
               _seg(3, 6, 8, "room", "what is that"),                       # the other track stays
               _seg(4, 31, 34, "mac", "next chunk")]                        # after the window stays
    gone = class_live.in_window(preview, "mac", 0, 30)
    assert [g["id"] for g in gone] == [1, 2]


def test_a_line_belongs_to_the_window_its_middle_is_in():
    straddle = _seg(9, 28, 34, "mac", "crosses the cut")                    # middle 31 -> the next window
    assert class_live.in_window([straddle], "mac", 0, 30) == []
    assert class_live.in_window([straddle], "mac", 30, 60) == [straddle]


def test_replace_swaps_preview_for_corrected_lines_and_keeps_labels(con, clock):
    sid = classes.start(con, clock, "Chem", None, None, "online")["id"]
    a = classes.add_segment(con, sid, 0, 4, "mac", "a mole is aboga dros number")
    b = classes.add_segment(con, sid, 5, 9, "mac", "of particles")
    me = classes.add_segment(con, sid, 6, 8, "room", "wait what")
    out = class_live.replace_window(con, sid, "mac", 0, 30, [
        {"t_start": 0.2, "t_end": 4.5, "text": "A mole is Avogadro's number"},
        {"t_start": 5.0, "t_end": 9.0, "text": "of particles."}])
    assert sorted(out["removed"]) == sorted([a, b])
    assert [s["text"] for s in out["added"]] == ["A mole is Avogadro's number", "of particles."]
    assert all(s["source"] == "mac" for s in out["added"])
    segs = classes.get(con, sid)["segments"]
    assert [(s["source"], s["text"]) for s in segs] == [
        ("mac", "A mole is Avogadro's number"), ("mac", "of particles."), ("room", "wait what")]
    assert me in [s["id"] for s in segs]


def test_nothing_read_never_wipes_good_preview_lines(con, clock):
    sid = classes.start(con, clock, "Chem", None, None, "online")["id"]
    classes.add_segment(con, sid, 0, 4, "mac", "the mole is a unit of amount")
    out = class_live.replace_window(con, sid, "mac", 0, 30, [])
    assert out == {"removed": [], "added": []}
    assert len(classes.get(con, sid)["segments"]) == 1


def test_the_replaced_preview_is_kept_beside_the_audio_for_comparing(con, clock, tmp_path):
    sid = classes.start(con, clock, "Chem", None, None, "online")["id"]
    classes.add_segment(con, sid, 0, 4, "mac", "aboga dros")
    d = tmp_path / "audio"
    d.mkdir()
    class_live.replace_window(con, sid, "mac", 0, 30, [{"t_start": 0, "t_end": 4, "text": "Avogadro's"}], keep_dir=d)
    kept = [json.loads(x) for x in (d / "live-preview.jsonl").read_text().splitlines()]
    assert [k["text"] for k in kept] == ["aboga dros"] and kept[0]["source"] == "mac"


# ---- a whole round --------------------------------------------------------------------------------------------------

def _tracks(tmp_path, sid, started, seconds, mac_start_off=0.0, mic_start_off=0.0, level=0.2):
    d = tmp_path / "class-audio" / sid
    d.mkdir(parents=True)
    for stem, off in (("class", mac_start_off), ("isaac", mic_start_off)):
        (d / f"{stem}.pcm").write_bytes((np.full(int(seconds * SR), level, np.float32) * 32767).astype("<i2").tobytes())
        (d / f"{stem}.start").write_text(str(started + off))
    return d


def _ctx(con, clock, tmp_path):
    return SimpleNamespace(con=con, clock=clock, paths=MistyPaths(tmp_path))


def _round(ctx, sid, stt):
    return asyncio.run(class_live.correct_round(ctx, sid, stt=stt))


def _model(mac=(), mic=(), calls=None):
    """Stands in for Phonon-2.5. It is handed the teacher chunk, a gap of silence and the mic chunk as ONE array (the
    model takes seconds just to load, so both tracks share one run) and answers in that array's time."""
    def stt(x):
        if calls is not None:
            calls.append(len(x))
        n = (len(x) - int(class_live.GAP_S * SR)) // 2
        off = n / SR + class_live.GAP_S
        return ([{"start": a, "end": b, "text": t} for a, b, t in mac]
                + [{"start": a + off, "end": b + off, "text": t} for a, b, t in mic])
    return stt


def test_round_reads_both_tracks_in_one_run_and_replaces_preview(con, clock, tmp_path):
    s = classes.start(con, clock, "Chem", None, None, "online")
    sid, t0 = s["id"], s["started_at"]
    _tracks(tmp_path, sid, t0, 70)
    classes.add_segment(con, sid, 1, 5, "mac", "aboga dros number")
    classes.add_segment(con, sid, 10, 12, "room", "wat is that")
    calls = []
    out = _round(_ctx(con, clock, tmp_path), sid,
                 _model(mac=[(1.0, 5.0, "Avogadro's number")], mic=[(10.0, 12.0, "What is that?")], calls=calls))
    assert len(calls) == 1                                                  # one model run for teacher and mic
    texts = {(s["source"], s["text"]) for s in classes.get(con, sid)["segments"]}
    assert ("mac", "Avogadro's number") in texts and ("room", "What is that?") in texts
    assert not any("aboga" in t for _, t in texts)
    assert out["changes"] and out["up_to"] > 45


def test_a_line_that_straddles_the_gap_is_clamped_inside_its_own_track():
    spans = [("class", 0.0, 50.0), ("isaac", 53.0, 103.0)]
    segs = [{"start": 48.0, "end": 51.0, "text": "tail of teacher"},          # runs past the teacher's end
            {"start": 51.5, "end": 70.0, "text": "starts in the gap"}]         # middle is inside the mic track
    out = class_live.split_lines(segs, spans)
    assert out["class"][0]["end"] == 50.0
    assert out["isaac"][0]["start"] == 0.0 and out["isaac"][0]["end"] == 17.0


def test_lines_are_handed_back_to_the_track_they_came_from():
    spans = [("class", 0.0, 50.0), ("isaac", 53.0, 103.0)]
    segs = [{"start": 4.0, "end": 8.0, "text": "teacher"}, {"start": 60.0, "end": 64.0, "text": "me"},
            {"start": 51.0, "end": 52.0, "text": "inside the gap"}]
    out = class_live.split_lines(segs, spans)
    assert out == {"class": [{"start": 4.0, "end": 8.0, "text": "teacher"}],
                   "isaac": [{"start": 7.0, "end": 11.0, "text": "me"}]}          # the mic's time starts at 0 again


def test_next_round_starts_where_the_last_one_stopped(con, clock, tmp_path):
    s = classes.start(con, clock, "Chem", None, None, "online")
    sid, t0 = s["id"], s["started_at"]
    d = _tracks(tmp_path, sid, t0, 70)
    ctx = _ctx(con, clock, tmp_path)
    calls = []
    first = _round(ctx, sid, _model(mac=[(0.0, 2.0, "line")], calls=calls))
    again = _round(ctx, sid, _model(mac=[(0.0, 2.0, "line")], calls=calls))   # nothing new has finished yet
    assert len(calls) == 1 and again["changes"] == []
    assert json.loads((d / "live-correct.json").read_text())["done"] == first["up_to"]


def test_a_round_that_cannot_read_leaves_the_preview_and_tries_again_later(con, clock, tmp_path):
    s = classes.start(con, clock, "Chem", None, None, "online")
    sid, t0 = s["id"], s["started_at"]
    _tracks(tmp_path, sid, t0, 70)
    classes.add_segment(con, sid, 1, 5, "mac", "keep me")
    out = _round(_ctx(con, clock, tmp_path), sid, lambda x: None)           # model missing or crashed
    assert out["changes"] == [] and out["up_to"] == 0
    assert [s["text"] for s in classes.get(con, sid)["segments"]] == ["keep me"]


def test_a_second_round_while_one_runs_is_skipped(con, clock, tmp_path):
    s = classes.start(con, clock, "Chem", None, None, "online")
    sid, t0 = s["id"], s["started_at"]
    _tracks(tmp_path, sid, t0, 70)
    ctx = _ctx(con, clock, tmp_path)

    async def both():
        gate = asyncio.Event()
        inner = []

        slow = _model(mac=[(0.0, 2.0, "line")], calls=inner)

        async def first():
            return await class_live.correct_round(ctx, sid, stt=slow)

        t = asyncio.create_task(first())
        await asyncio.sleep(0)                                              # first round has taken the slot
        second = await class_live.correct_round(ctx, sid, stt=slow)
        gate.set()
        return await t, second

    _, second = asyncio.run(both())
    assert second.get("busy") is True and second["changes"] == []


def test_a_class_that_is_not_live_gets_no_live_round(con, clock, tmp_path):
    s = classes.start(con, clock, "Chem", None, None, "online")
    sid, t0 = s["id"], s["started_at"]
    _tracks(tmp_path, sid, t0, 70)
    classes.end(con, clock, sid)
    out = _round(_ctx(con, clock, tmp_path), sid, _model(mac=[(0.0, 1.0, "x")]))
    assert out["changes"] == []


def test_the_teachers_voice_coming_back_out_of_the_speakers_is_not_logged_as_isaac(con, clock, tmp_path):
    s = classes.start(con, clock, "Chem", None, None, "online")
    sid, t0 = s["id"], s["started_at"]
    _tracks(tmp_path, sid, t0, 70)
    _round(_ctx(con, clock, tmp_path), sid, _model(
        mac=[(2.0, 6.0, "a mole is avogadro's number of particles")],
        mic=[(2.0, 6.0, "A mole is Avogadro's number of particles")]))
    assert [s["source"] for s in classes.get(con, sid)["segments"]] == ["mac"]


def test_a_track_that_starts_late_is_lined_up_on_class_time(con, clock, tmp_path):
    s = classes.start(con, clock, "Chem", None, None, "online")
    sid, t0 = s["id"], s["started_at"]
    _tracks(tmp_path, sid, t0, 70, mic_start_off=2.0)                       # the mic opened 2 s after the class began
    out = _round(_ctx(con, clock, tmp_path), sid, _model(mac=[(1.0, 2.0, "teacher")], mic=[(5.0, 6.0, "me")]))
    segs = {s["source"]: s for s in classes.get(con, sid)["segments"]}
    assert segs["mac"]["t_start"] == 1.0 and out["changes"]


def test_the_model_runs_at_background_priority_with_a_fallback(monkeypatch, tmp_path):
    real = tmp_path / "taskpolicy"
    real.write_text("")
    monkeypatch.setattr(class_live, "LOW_PRIORITY", ([str(tmp_path / "missing"), "-b"], [str(real), "-n", "19"]))
    assert class_live.low_priority() == [str(real), "-n", "19"]              # first one this Mac actually has
    monkeypatch.setattr(class_live, "LOW_PRIORITY", ([str(tmp_path / "missing"), "-b"],))
    assert class_live.low_priority() == []                                  # none: runs normally, never fails
    assert Path("/usr/sbin/taskpolicy").exists() or Path("/usr/bin/nice").exists()
