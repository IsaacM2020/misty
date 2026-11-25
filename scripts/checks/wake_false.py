#!/usr/bin/env python3
"""Does the "Edith" wake word fire when it should not, and does it catch the real word? Runs the app's own spotter
(app/Misty/Core/Voice/WakeWord.swift, via scripts/checks/wake_probe.swift: same sherpa-onnx library, same model, same
tuning) over the audio already recorded in ~/Misty and prints detections per hour.

  audio checked for false wakes : class-audio/*/*.m4a (his mic track and the class track), dictation-samples/*.wav,
                                  ttsdump/*.wav (EDITH's own voice), dictation-keep/ when it holds audio
  real "Edith"s                 : transcripts in misty.db / dictation-samples that contain the word, matched to audio when
                                  there is audio; plus synthetic "Edith" clips made with the Mac's own voices (not his voice)

Needs scripts/fetch_wake_model.sh to have run. Usage:
  scripts/checks/wake_false.py [--sens 0.5 | --score 2.0 --thr 0.05] [--jobs 4] [--hours N] [--no-synth] [--home ~/Misty]
Free and local: it reads audio and runs a 6 MB model; nothing leaves the Mac.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SWIFT_DEV = Path("/Applications/Xcode.app/Contents/Developer")
SYNTH_VOICES = ["Samantha", "Daniel", "Karen", "Rishi", "Moira", "Aman", "Eddy (English (US))", "Flo (English (US))",
                "Reed (English (UK))", "Shelley (English (US))"]
SYNTH_LINES = ["Edith", "Edith, what's next today?", "Hey Edith, remind me about physics"]


def build_probe(out: Path) -> Path:
    vendor = ROOT / "app/Vendor/sherpa-onnx"
    if not (vendor / "wake.xcconfig").exists():
        sys.exit("sherpa-onnx library missing: run scripts/fetch_wake_model.sh first")
    flags = re.search(r"OTHER_LDFLAGS = \$\(inherited\) (.*)", (vendor / "wake.xcconfig").read_text()).group(1).split()
    srcs = [ROOT / "app/Misty/Core/Voice/WakeWord.swift", ROOT / "app/Misty/Core/Voice/Endpointer.swift",
            ROOT / "scripts/checks/wake_probe.swift"]
    if out.exists() and out.stat().st_mtime >= max(s.stat().st_mtime for s in srcs):
        return out
    sdk = sorted((SWIFT_DEV / "Platforms/MacOSX.platform/Developer/SDKs").glob("MacOSX*.sdk"))[-1]
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(SWIFT_DEV / "Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc"), "-sdk", str(sdk), "-swift-version", "6", "-O",
           "-parse-as-library", "-I", str(vendor / "include"), "-L", str(vendor / "lib"), *flags, "-o", str(out), *map(str, srcs)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("probe build failed:\n" + r.stderr[-2000:])
    return out


def probe(binary: Path, files: list[str], extra: list[str], home: str) -> list[dict]:
    r = subprocess.run([str(binary), "--home", home, *extra, *files], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        try:
            d = json.loads(line)
            d["hits"] = [float(h) for h in d.get("hits", [])]      # the probe prints them as strings
            out.append(d)
        except json.JSONDecodeError:
            pass
    return out


def run_all(binary, files, extra, home, jobs):
    """One probe process per file, `jobs` at a time (each spotter is one thread)."""
    with ThreadPoolExecutor(jobs) as ex:
        res = list(ex.map(lambda f: (probe(binary, [f], extra, home) or [{"file": f, "error": "no output"}])[0], files))
    return res


def segments_near(con, session: str, t: float) -> str:
    rows = con.execute("SELECT source, text FROM class_segments WHERE session_id=? AND t_start<=? AND t_end>=? LIMIT 2",
                       (session, t, t)).fetchall()
    return " | ".join(f"[{s}] {x}" for s, x in rows) or "(no transcript there)"


def synth_clips(tmp: str) -> list[tuple[str, str]]:
    clips = []
    for i, (v, line) in enumerate((v, l) for v in SYNTH_VOICES for l in SYNTH_LINES):
        aiff, wav = f"{tmp}/s{i}.aiff", f"{tmp}/s{i}.wav"
        if subprocess.run(["say", "-v", v, "-o", aiff, line], capture_output=True).returncode:
            continue
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", aiff, "-ar", "16000", "-ac", "1", wav], capture_output=True)
        if os.path.exists(wav):
            clips.append((wav, f"{v}: {line}"))
    return clips


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.path.expanduser("~/Misty"))
    ap.add_argument("--sens", type=float, default=0.5)
    ap.add_argument("--score", type=float)
    ap.add_argument("--thr", type=float)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--hours", type=float, help="stop after about this many hours of audio (quick run)")
    ap.add_argument("--no-synth", action="store_true")
    ap.add_argument("--probe", default=str(ROOT / "build/wake_probe"))
    a = ap.parse_args()
    home = a.home
    extra = ["--sens", str(a.sens)] if a.score is None else ["--score", str(a.score), "--thr", str(a.thr)]
    binary = build_probe(Path(a.probe))

    groups = {
        "class mic track (his room)": sorted(glob.glob(f"{home}/class-audio/*/isaac.m4a")),
        "class Mac-sound track (teacher)": sorted(glob.glob(f"{home}/class-audio/*/class.m4a")),
        "dictation samples": sorted(glob.glob(f"{home}/dictation-samples/*.wav")),
        "EDITH's own voice (ttsdump)": sorted(glob.glob(f"{home}/ttsdump/*.wav")),
        "dictation-keep": [p for p in glob.glob(f"{home}/dictation-keep/**/*", recursive=True) if os.path.isfile(p) and os.path.getsize(p) > 0],
    }
    con = sqlite3.connect(f"file:{home}/misty.db?mode=ro", uri=True) if os.path.exists(f"{home}/misty.db") else None

    print(f"wake word check: {'sensitivity ' + str(a.sens) if a.score is None else f'score {a.score} threshold {a.thr}'}\n")
    total_s, total_hits, results = 0.0, 0, {}
    budget = a.hours * 3600 if a.hours else None
    for name, files in groups.items():
        if not files:
            continue
        if budget is not None:
            files = files[: max(1, int(len(files) * min(1.0, budget / max(1, sum(os.path.getsize(f) for f in files) / 16000 / 2))))]
        res = run_all(binary, files, extra, home, a.jobs)
        secs = sum(r.get("seconds", 0) for r in res)
        hits = sum(len(r.get("hits", [])) for r in res)
        cpu = sum(r.get("cpu_s", 0) for r in res)
        bad = [r for r in res if "error" in r]
        total_s += secs; total_hits += hits
        results[name] = res
        per_h = hits / (secs / 3600) if secs else 0
        print(f"{name:34s} {secs / 3600:6.2f} h  {hits:3d} detections  {per_h:5.2f}/h  spotter cpu {100 * cpu / max(secs, 1):.2f}% of one core"
              + (f"  ({len(bad)} unreadable)" if bad else ""))
    print(f"\nALL FALSE-WAKE AUDIO: {total_s / 3600:.2f} h, {total_hits} detections = {total_hits / max(total_s / 3600, 1e-9):.2f} per hour\n")

    print("every detection (review these: a real \"Edith\" is not a false wake):")
    for name, res in results.items():
        for r in res:
            for t in r.get("hits", []):
                where = ""
                m = re.search(r"class-audio/(cs_[0-9a-f]+)/", r["file"])
                if con and m:
                    where = segments_near(con, m.group(1), t)
                elif r["file"].endswith(".wav") and os.path.exists(r["file"][:-4] + ".txt"):
                    where = Path(r["file"][:-4] + ".txt").read_text()[:160].replace("\n", " ")
                print(f"  {name.split(' (')[0]:28s} {os.path.relpath(r['file'], home)} @ {t:.1f}s  {where}")

    print("\nreal \"Edith\" in what was recorded:")
    n_real = 0
    if con:
        for table, col in (("class_segments", "text"), ("heard_phrases", "text"), ("chat_messages", "text")):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE lower({col}) LIKE '%edith%'").fetchone()[0]
            except sqlite3.Error:
                continue
            print(f"  misty.db {table}: {n} rows with the word")
    for txt in glob.glob(f"{home}/dictation-samples/*.txt"):
        if "edith" in Path(txt).read_text().lower():
            n_real += 1
            wav = txt[:-4] + ".wav"
            r = next((x for x in results.get("dictation samples", []) if x["file"] == wav), None)
            what = ("caught at " + ", ".join(f"{t:.1f}s" for t in r["hits"])) if r and r.get("hits") else "NOT caught"
            body = Path(txt).read_text().split("pasted:")[0]
            i = body.lower().find("edith")
            print(f"  dictation sample {os.path.basename(wav)}: said while talking ABOUT her, not calling her (\"...{body[max(0, i - 40):i + 30].strip()}...\"): {what}")
    if not n_real:
        print("  no recorded audio of him saying it")

    if not a.no_synth:
        with tempfile.TemporaryDirectory() as tmp:
            clips = synth_clips(tmp)
            res = run_all(binary, [c for c, _ in clips], extra, home, a.jobs)
            caught = sum(1 for r in res if r.get("hits"))
            print(f"\nsynthetic \"Edith\" (the Mac's {len(SYNTH_VOICES)} voices x {len(SYNTH_LINES)} phrases, NOT his voice): caught {caught} of {len(clips)}")
            for line in SYNTH_LINES:
                idx = [i for i, (_, d) in enumerate(clips) if d.endswith(": " + line)]
                print(f"  {line!r:42s} {sum(1 for i in idx if res[i].get('hits'))}/{len(idx)}")


if __name__ == "__main__":
    main()
