"""The after-class pass for an online class: Phonon-2.5 English, on the Mac, over each recorded track.
(Whisper large-v3-turbo did this job until 2026-11-15 and is still the fallback. On a 58 minute class Phonon took
59 s against Whisper's 304 s, so the Mac runs hot for a fifth of the time, and it does not invent "Thank you." on
quiet audio.)

The app writes two tracks while the class runs (`~/Misty/class-audio/<session>/`): `class.m4a` is the Mac's sound
(the teacher and classmates on the call), `isaac.m4a` is the mic. `tracks.json` says when each one started. The
live Phonon transcript is only a preview; once the class ends each track is read on its own, so the label comes
from the track and nothing has to guess who spoke. Both go on one timeline from the class start, and a mic phrase
that is just the teacher coming back out of the speakers is dropped (the app's `isTeacherEcho` rule).

Free and private: the model runs locally and the audio never leaves the Mac.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

MODEL = "mlx-community/whisper-large-v3-turbo"         # the fallback
PHONON = "phonon-2.5-en"                               # fermion-research's name for FermionResearch/Phonon-2.5-English
PHONON_TIMEOUT_S = 1800
TRACKS = {"class": "mac", "isaac": "room"}           # file stem -> class_segments.source
ECHO_WINDOW_S = 12.0
# Whisper's habit on quiet audio: stock phrases from its subtitle training data, never said in a class
STOCK = re.compile(r"^(thank you\.?|thanks for watching[.!]?|thank you for watching[.!]?|subtitles by .*|"
                   r"please subscribe.*|bye\.?|you)$", re.I)


def audio_dir(home: Path, sid: str) -> Path:
    return home / "class-audio" / sid


def has_tracks(home: Path, sid: str) -> bool:
    return (audio_dir(home, sid) / "tracks.json").exists()


def _load(path: Path) -> np.ndarray:
    """An m4a track as 16 kHz mono float32 (afconvert ships with macOS, so the bundled engine needs no ffmpeg)."""
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "t.wav"
        subprocess.run(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(path), str(wav)],
                       check=True, capture_output=True)
        with wave.open(str(wav)) as w:
            raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _keep(seg: dict) -> bool:
    text = seg["text"].strip()
    if not text or STOCK.match(text):
        return False
    if seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0:
        return False
    return seg.get("compression_ratio", 0) <= 2.4                # a loop ("the the the ...") is not speech


FERMION_ENV = Path.home() / "Library/Application Support/Misty/fermion"     # made by scripts/fetch_class_model.sh


def _fermion() -> list[str] | None:
    """How to start the `fermion` command line: its own small Python environment outside the app. It cannot live in
    the app's bundled engine, because signing the app re-signs its speech library and the package then refuses to
    load it (it checks the file's sha256 against a pin)."""
    py = Path(os.environ.get("MISTY_FERMION_PY") or FERMION_ENV / "bin/python")
    if not py.exists():
        return None
    return [str(py), "-c", "import sys; from fermion.cli import main; sys.exit(main())"]


MINI_HOME = Path.home() / "Library/Application Support/Misty/fluid-mini"      # made by scripts/fetch_fluid_mini.sh
MINI_TIMEOUT_S = 3 * 3600


def _mini_cmd() -> list[str] | None:
    """Fluid Speech Mini (ONNX on the CPU) in its own Python environment outside the app, like Phonon. None when not set up."""
    home = Path(os.environ.get("MISTY_MINI_HOME") or MINI_HOME)
    py = home / "venv/bin/python"
    if not (py.exists() and (home / "segments.py").exists() and (home / "model/encoder.onnx").exists()):
        return None
    return [str(py), str(home / "segments.py"), str(home / "model")]


def _mini(samples: np.ndarray, cmd: list[str] | None = None) -> list[dict] | None:
    """The track read by Fluid Speech Mini. None means "try the next reader": not set up, MISTY_CLASS_STT names another
    reader, or the run failed. About 30 second pieces, each with its real start and end."""
    cmd = cmd or _mini_cmd()
    if os.environ.get("MISTY_CLASS_STT") in ("whisper", "phonon") or cmd is None:
        return None
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "t.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
        try:
            got = subprocess.run([*cmd, str(wav)], capture_output=True, text=True, timeout=MINI_TIMEOUT_S, check=True)
            segs = json.loads(got.stdout)["segments"]
            return [{"start": float(s["start"]), "end": float(s["end"]), "text": " ".join(s["text"].split())}
                    for s in segs if s["text"].strip()]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            return None


def _phonon(samples: np.ndarray, cmd: list[str] | None = None, timeout: float = PHONON_TIMEOUT_S) -> list[dict] | None:
    """The track read by Phonon-2.5 English in a child process.
    None means "use Whisper": the package is missing, MISTY_CLASS_STT=whisper, or the run failed."""
    cmd = cmd or _fermion()
    if os.environ.get("MISTY_CLASS_STT") in ("whisper", "mini") or cmd is None:
        return None
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "t.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
        try:
            got = subprocess.run([*cmd, "transcribe", PHONON, str(wav), "--json"], capture_output=True, text=True,
                                 timeout=timeout, check=True)
            segs = json.loads(got.stdout)["segments"]
            return [{"start": float(s["start"]), "end": float(s["end"]), "text": " ".join(s["text"].split())}
                    for s in segs if s["text"].strip()]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            return None


def transcribe(samples: np.ndarray, prompt: str | None = None, run=None) -> list[dict]:
    """[{start, end, text}] in seconds from the track's start. `run` is for tests (the real model is 1.6 GB)."""
    if samples.size < 16000 or float(np.abs(samples).max(initial=0)) < 1e-4:
        return []                                         # under a second, or digital silence: nothing to read
    if run is None:
        if (got := _mini(samples)) is not None:                 # Fluid Speech Mini, then Phonon-2.5, then Whisper
            return got
        if (got := _phonon(samples)) is not None:
            return got
        import mlx_whisper
        run = mlx_whisper.transcribe
    out = run(samples, path_or_hf_repo=MODEL, language="en", condition_on_previous_text=False,
              word_timestamps=True, hallucination_silence_threshold=2.0, initial_prompt=prompt)
    return [{"start": float(s["start"]), "end": float(s["end"]), "text": " ".join(s["text"].split())}
            for s in out.get("segments", []) if _keep(s)]


def words(s: str) -> list[str]:
    """Same split as the app's `VoiceIntent.words`: letters and apostrophes, two letters or more."""
    return [w for w in re.split(r"[^a-z']+", s.lower()) if len(w) >= 2]


FILLERS = {"uh", "um", "umm", "uhm", "hmm", "hm", "mm", "mhm", "ah", "oh", "eh", "er", "huh"}   # the app's list


def is_teacher_echo(heard: str, teacher: list[str]) -> bool:
    w = [x for x in words(heard) if x not in FILLERS]
    if len(w) < 3:
        return False
    theirs = set(teacher)
    return sum(x in theirs for x in w) / len(w) >= 0.8


def merge(cls: list[dict], me: list[dict]) -> list[dict]:
    """One timeline of {t_start, t_end, source, text}, both lists already in class time. Echo of the class on the
    mic (speakers, no headphones) is dropped."""
    out = [{"t_start": s["start"], "t_end": s["end"], "source": "mac", "text": s["text"]} for s in cls]
    for s in me:
        near = [w for c in cls if c["end"] >= s["start"] - ECHO_WINDOW_S and c["start"] <= s["end"] + 1
                for w in words(c["text"])]
        if not is_teacher_echo(s["text"], near):
            out.append({"t_start": s["start"], "t_end": s["end"], "source": "room", "text": s["text"]})
    out.sort(key=lambda x: (x["t_start"], x["source"]))
    return out


def read_class(home: Path, sid: str, started_at: float, prompt: str | None = None, run=None) -> list[dict] | None:
    """Both tracks of a class read, shifted to class time and merged. None when there are no tracks."""
    d = audio_dir(home, sid)
    try:
        meta = json.loads((d / "tracks.json").read_text())
    except (OSError, ValueError):
        return None
    sides: dict[str, list[dict]] = {"class": [], "isaac": []}
    audio = {stem: _load(d / f"{stem}.m4a") for stem in TRACKS
             if meta.get(stem) and (d / f"{stem}.m4a").exists()}
    if "class" in audio and "isaac" in audio:              # speakers on: take the class out of the mic first
        from . import echo_cancel
        offset = round((float(meta["isaac"]["started"]) - float(meta["class"]["started"])) * echo_cancel.SR)
        audio["isaac"], info = echo_cancel.suppress(audio["isaac"], audio["class"], offset)
        meta["echo"] = info
        try:
            (d / "tracks.json").write_text(json.dumps(meta, indent=1))
        except OSError:
            pass
    for stem, samples in audio.items():
        shift = float(meta[stem]["started"]) - started_at
        sides[stem] = [{**s, "start": s["start"] + shift, "end": s["end"] + shift}
                       for s in transcribe(samples, prompt, run)]
    from . import subject_words
    out = merge(sides["class"], sides["isaac"])
    for s in out:                                         # his own `heard => wanted` fixes, like dictation
        s["text"] = subject_words.apply_dictionary(home, s["text"])
    return out


def recorded_windows(home: Path, sid: str, started_at: float) -> dict[str, tuple[float, float]]:
    """{source: (from, to)} in class time of what each track on disk holds. The app starts a new recorder when a
    paused class resumes (lid closed), which overwrites the files, so a track can cover only the end of the class."""
    d = audio_dir(home, sid)
    try:
        meta = json.loads((d / "tracks.json").read_text())
        out = {}
        for stem, source in TRACKS.items():
            m = meta.get(stem)
            if m and (d / f"{stem}.m4a").exists():
                lo = float(m["started"]) - started_at
                out[source] = (lo, lo + float(m["seconds"]))
        return out
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def outside_recording(live: list[dict], windows: dict[str, tuple[float, float]]) -> list[dict]:
    """The live-preview segments that lie wholly outside what their track recorded: the audio pass can't read them,
    so they stay. (Segments overlapping the recording are replaced by what Whisper reads.)"""
    return [g for g in live if g["source"] in windows
            and (g["t_end"] <= windows[g["source"]][0] or g["t_start"] >= windows[g["source"]][1])]


def misty_home() -> Path:
    return Path(os.environ.get("MISTY_HOME", Path.home() / "Misty"))
