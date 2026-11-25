"""The after-class pass: each recorded track read on its own, labels from the track, both on the class timeline,
the teacher's echo on the mic dropped, Whisper's stock phrases on quiet audio dropped, and the live preview
replaced (kept beside the audio) before the notes are made."""
import asyncio
import json
from types import SimpleNamespace

import numpy as np

from misty_engine import class_audio, class_notes, classes
from misty_engine.paths import MistyPaths

from test_class_notes import FRI, FakeRunner


def seg(start, end, text, **kw):
    return {"start": start, "end": end, "text": text, "no_speech_prob": 0.01, "avg_logprob": -0.2,
            "compression_ratio": 1.3, **kw}


def test_stock_phrases_silence_and_loops_are_dropped():
    out = class_audio.transcribe(np.full(32000, 0.1, np.float32), run=lambda *a, **k: {"segments": [
        seg(0, 2, " Thank you."), seg(2, 4, "Moles measure amount."),
        seg(4, 6, "uh", no_speech_prob=0.9, avg_logprob=-1.5), seg(6, 8, "the the the the", compression_ratio=3.1)]})
    assert [s["text"] for s in out] == ["Moles measure amount."]


def test_silent_or_tiny_track_is_not_read():
    def boom(*a, **k):
        raise AssertionError("model called")
    assert class_audio.transcribe(np.zeros(16000 * 30, np.float32), run=boom) == []
    assert class_audio.transcribe(np.full(8000, 0.2, np.float32), run=boom) == []


def test_merge_labels_by_track_sorts_and_drops_teacher_echo():
    cls = [{"start": 0.0, "end": 5.0, "text": "A mole is Avogadro's number of particles"},
           {"start": 20.0, "end": 24.0, "text": "Homework is page 112"}]
    me = [{"start": 1.0, "end": 5.5, "text": "a mole is Avogadro's number of particles"},   # speakers coming back
          {"start": 10.0, "end": 12.0, "text": "Wait, what is a mole exactly?"},              # him, reusing her terms
          {"start": 40.0, "end": 41.0, "text": "Avogadro's number of particles"}]             # same words, 35 s later
    out = class_audio.merge(cls, me)
    assert [(s["source"], s["text"]) for s in out] == [
        ("mac", "A mole is Avogadro's number of particles"),
        ("room", "Wait, what is a mole exactly?"),
        ("mac", "Homework is page 112"),
        ("room", "Avogadro's number of particles")]


def test_tracks_are_shifted_to_class_time(tmp_path, monkeypatch):
    d = tmp_path / "class-audio" / "cs_x"
    d.mkdir(parents=True)
    (d / "class.m4a").write_bytes(b"x")
    (d / "isaac.m4a").write_bytes(b"x")
    (d / "tracks.json").write_text(json.dumps({"class": {"started": 1002.0, "seconds": 60},
                                               "isaac": {"started": 1005.0, "seconds": 60}}))
    monkeypatch.setattr(class_audio, "_load", lambda f: np.full(32000, 0.1, np.float32) if "class" in f.name
                        else np.full(32000, 0.2, np.float32))
    run = lambda a, **k: {"segments": [seg(1, 3, "teacher line" if a[0] < 0.15 else "my own question here")]}
    out = class_audio.read_class(tmp_path, "cs_x", 1000.0, run=run)
    assert [(s["source"], s["t_start"]) for s in out] == [("mac", 3.0), ("room", 6.0)]


def test_notes_use_the_after_class_transcript_and_keep_the_preview(con, clock, settings, tmp_path, monkeypatch):
    clock.t = FRI
    ctx = SimpleNamespace(con=con, clock=clock, settings=settings, runner=FakeRunner(), wiki=None, notify=None,
                          paths=MistyPaths(tmp_path))
    s = classes.start(con, clock, "Chem", None, None, mode="online")["id"]
    classes.add_segment(con, s, 0, 5, "room", "garbled preview words")
    classes.end(con, clock, s)
    d = class_audio.audio_dir(tmp_path, s)
    d.mkdir(parents=True)
    (d / "tracks.json").write_text("{}")
    long = " ".join(["equilibrium shifts to oppose the stress"] * 30)
    monkeypatch.setattr(class_audio, "read_class", lambda home, sid, t0, prompt=None, run=None: [
        {"t_start": 0, "t_end": 9, "source": "mac", "text": long},
        {"t_start": 9, "t_end": 11, "source": "room", "text": "is that on the test?"}])
    assert asyncio.run(class_notes.make_notes(ctx, s)) == "noted"
    got = classes.get(con, s)["segments"]
    assert [g["source"] for g in got] == ["mac", "room"]
    assert json.loads((d / "preview.json").read_text())[0]["text"] == "garbled preview words"
    assert "Teacher: " in "".join(ctx.runner.calls[0]["parts"])
    # a Regenerate doesn't read the audio again
    monkeypatch.setattr(class_audio, "read_class", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-read")))
    asyncio.run(class_notes.make_notes(ctx, s))


def _paused_class(con, clock, settings, tmp_path, monkeypatch):
    """An online class where the lid closed at 10:00 and opened at 20:00 (class time): the app starts a new recorder
    on resume, which overwrites class.m4a / isaac.m4a, so the files only hold what came after."""
    clock.t = FRI
    ctx = SimpleNamespace(con=con, clock=clock, settings=settings, runner=FakeRunner(), wiki=None, notify=None,
                          paths=MistyPaths(tmp_path))
    s = classes.start(con, clock, "Chem", None, None, mode="online")["id"]
    early = " ".join(["moles"] * 40)
    classes.add_segment(con, s, 0, 20, "mac", "Before the lid closed the teacher explained " + early)
    classes.add_segment(con, s, 20, 30, "room", "wait is a mole a number")
    classes.add_segment(con, s, 1200, 1220, "mac", "garbled preview of the second half")
    classes.end(con, clock, s)
    d = class_audio.audio_dir(tmp_path, s)
    d.mkdir(parents=True)
    for f in ("class.m4a", "isaac.m4a"):
        (d / f).write_bytes(b"x")
    (d / "tracks.json").write_text(json.dumps({"class": {"started": FRI + 1190, "seconds": 600.0},
                                               "isaac": {"started": FRI + 1190, "seconds": 600.0}}))
    second = " ".join(["equilibrium shifts to oppose the stress"] * 30)
    monkeypatch.setattr(class_audio, "read_class", lambda home, sid, t0, prompt=None, run=None: [
        {"t_start": 1200.0, "t_end": 1220.0, "source": "mac", "text": second}])
    return ctx, s, d


def test_a_pause_that_restarted_the_recorder_keeps_the_preview_of_the_part_not_on_disk(con, clock, settings, tmp_path,
                                                                                          monkeypatch):
    ctx, s, d = _paused_class(con, clock, settings, tmp_path, monkeypatch)
    assert asyncio.run(class_notes.make_notes(ctx, s)) == "noted"
    got = classes.get(con, s)["segments"]
    assert [(g["t_start"], g["source"]) for g in got] == [(0, "mac"), (20, "room"), (1200.0, "mac")]
    assert got[0]["text"].startswith("Before the lid closed") and got[1]["text"] == "wait is a mole a number"
    assert got[2]["text"].startswith("equilibrium shifts")            # the recorded part comes from the audio, not the preview
    assert "Before the lid closed" in "".join(ctx.runner.calls[0]["parts"])
    assert [p["text"][:12] for p in json.loads((d / "preview.json").read_text())] == ["Before the l", "wait is a mo", "garbled prev"]


def test_recorded_windows_are_class_time_per_source(tmp_path):
    d = class_audio.audio_dir(tmp_path, "cs_w")
    d.mkdir(parents=True)
    (d / "class.m4a").write_bytes(b"x")                    # isaac.m4a is missing: no window for the mic
    (d / "tracks.json").write_text(json.dumps({"class": {"started": 1010.0, "seconds": 90.0},
                                               "isaac": {"started": 1000.0, "seconds": 100.0}}))
    assert class_audio.recorded_windows(tmp_path, "cs_w", 1000.0) == {"mac": (10.0, 100.0)}
    assert class_audio.recorded_windows(tmp_path, "cs_none", 1000.0) == {}
    live = [{"t_start": 0, "t_end": 9, "source": "mac"}, {"t_start": 5, "t_end": 12, "source": "mac"},
            {"t_start": 95, "t_end": 99, "source": "mac"}, {"t_start": 100, "t_end": 104, "source": "mac"},
            {"t_start": 0, "t_end": 9, "source": "room"}]
    assert class_audio.outside_recording(live, {"mac": (10.0, 100.0)}) == [live[0], live[3]]


def test_phonon_reads_the_track_and_whisper_is_the_fallback(tmp_path, monkeypatch):
    import json
    import subprocess
    from types import SimpleNamespace
    exe = ["py", "-c", "x"]
    seen = []

    def fake(cmd, **kw):
        seen.append(cmd)
        return SimpleNamespace(stdout=json.dumps({"segments": [
            {"id": 0, "start": 1.0, "end": 2.5, "text": " Newton's  second law "}, {"id": 1, "start": 3, "end": 4, "text": " "}]}))
    monkeypatch.setattr(subprocess, "run", fake)
    out = class_audio._phonon(np.full(32000, 0.1, np.float32), exe)
    assert out == [{"start": 1.0, "end": 2.5, "text": "Newton's second law"}]
    assert seen[0][:5] == ["py", "-c", "x", "transcribe", "phonon-2.5-en"] and seen[0][-1] == "--json"

    def broken(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(subprocess, "run", broken)
    assert class_audio._phonon(np.full(32000, 0.1, np.float32), exe) is None            # failed run: Whisper takes over
    monkeypatch.setattr(class_audio, "_fermion", lambda: None)                           # package not installed
    assert class_audio._phonon(np.full(32000, 0.1, np.float32)) is None
    monkeypatch.setenv("MISTY_CLASS_STT", "whisper")
    assert class_audio._phonon(np.full(32000, 0.1, np.float32), exe) is None


def test_mini_reads_the_track_then_phonon_then_whisper(tmp_path, monkeypatch):
    import json
    import subprocess
    from types import SimpleNamespace
    exe = ["py", "segments.py", "model"]

    def fake(cmd, **kw):
        return SimpleNamespace(stdout=json.dumps({"segments": [{"start": 0.0, "end": 30.0, "text": " Stefan Boltzmann  law "},
                                                               {"start": 30.0, "end": 41.0, "text": " "}]}))
    monkeypatch.setattr(subprocess, "run", fake)
    out = class_audio._mini(np.full(32000, 0.1, np.float32), exe)
    assert out == [{"start": 0.0, "end": 30.0, "text": "Stefan Boltzmann law"}]

    def broken(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(subprocess, "run", broken)
    assert class_audio._mini(np.full(32000, 0.1, np.float32), exe) is None               # failed: Phonon takes over
    monkeypatch.setattr(class_audio, "_mini_cmd", lambda: None)                          # not set up
    assert class_audio._mini(np.full(32000, 0.1, np.float32)) is None
    monkeypatch.setenv("MISTY_CLASS_STT", "phonon")
    assert class_audio._mini(np.full(32000, 0.1, np.float32), exe) is None


def test_order_is_mini_then_phonon_then_whisper(monkeypatch):
    calls = []
    monkeypatch.setattr(class_audio, "_mini", lambda s: calls.append("mini") or None)
    monkeypatch.setattr(class_audio, "_phonon", lambda s: calls.append("phonon") or [{"start": 0, "end": 1, "text": "from phonon"}])
    assert class_audio.transcribe(np.full(32000, 0.1, np.float32))[0]["text"] == "from phonon"
    assert calls == ["mini", "phonon"]
