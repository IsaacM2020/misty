#!/usr/bin/env python3
"""Season 3 Day 1: where the time goes before Misty's first spoken word.

Reads ~/Misty/logs/voice.log, joins each voice turn's app line (TURN_TIMING) with the engine's (ENGINE_TIMING) by
turn id, and prints the slices in ms from the tap that ended Isaac's speech to the sound leaving the speakers.

    scripts/turn_timing_report.py [--since 2026-11-07T06:14] [--log PATH]

A turn is "cold" within 150 s of the app launching, "wake" within 150 s of the Mac waking, otherwise "warm".
Numbers only. Needs no network and prints nothing from the conversation.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

LINE = re.compile(r"^(\S+) (TURN_TIMING|ENGINE_TIMING|TALK_START|WAKE)\b(.*)$")
SLICES = [
    ("hear", "Whisper reads the recording"),
    ("to_engine", "app -> engine hand-off"),
    ("prep", "engine prep (prompt, lock, checks)"),
    ("connect", "OpenRouter connect + headers"),
    ("model", "model: headers -> first token"),
    ("tools", "tool rounds before the first word"),
    ("event", "engine -> app event"),
    ("clause", "wait for a full clause"),
    ("voice_wait", "voice queue / warm"),
    ("audio_start", "audio engine start"),
    ("synth", "voice makes the first audio"),
    ("out", "speaker output latency"),
]


def kv(s: str) -> dict[str, str]:
    return dict(p.split("=", 1) for p in s.split() if "=" in p)


def num(d: dict, k: str):
    try:
        return float(d[k])
    except (KeyError, ValueError):
        return None


def parse(path: Path, since: str):
    app, eng, starts, wakes = {}, {}, [], []
    for raw in path.read_text(errors="replace").splitlines():
        m = LINE.match(raw)
        if not m or m.group(1) < since:
            continue
        stamp, kind, rest = m.groups()
        d = kv(rest)
        if kind == "TURN_TIMING":
            app[d["turn"]] = d
        elif kind == "ENGINE_TIMING":
            eng[d["turn"]] = d
        elif kind == "TALK_START":
            starts.append((stamp, d))
        else:
            wakes.append(stamp)
    return app, eng, starts, wakes


def first(d: dict, *names):
    for n in names:
        v = num(d, n)
        if v is not None:
            return v
    return None


def slices(a: dict, e: dict | None) -> dict[str, float | None]:
    g = lambda k: num(a, k)
    out: dict[str, float | None] = {k: None for k, _ in SLICES}
    if g("transcript") is not None and g("mic_stopped") is not None:
        out["hear"] = g("transcript") - g("mic_stopped")
    if e is not None:
        t_send_epoch = (num(a, "t_tap") or 0) + (g("send") or 0) / 1000
        out["to_engine"] = (num(e, "t0") - t_send_epoch) * 1000
        http0 = num(e, "r0_http_start")
        hdr = num(e, "r0_headers")
        tok = first(e, "r0_first_token", "r0_first_tool_token")
        out["prep"] = http0
        if http0 is not None and hdr is not None:
            out["connect"] = hdr - http0
        if hdr is not None and tok is not None:
            out["model"] = tok - hdr
        fd = num(e, "first_delta_out")
        if fd is not None and tok is not None:
            out["tools"] = max(0.0, fd - tok)
        if fd is not None and g("first_delta") is not None:
            out["event"] = g("first_delta") - ((num(e, "t0") + fd / 1000 - num(a, "t_tap")) * 1000)
    if g("first_clause") is not None and g("first_delta") is not None:
        out["clause"] = g("first_clause") - g("first_delta")
    if g("speak_called") is not None and g("drain_start") is not None:
        out["voice_wait"] = g("drain_start") - g("speak_called")
    if g("drain_start") is not None and g("engine_ready") is not None:
        out["audio_start"] = g("engine_ready") - g("drain_start")
    if g("engine_ready") is not None and g("first_chunk") is not None:
        out["synth"] = g("first_chunk") - g("engine_ready")
    out["out"] = num(a, "out_latency")
    return out


def tool_ms(e: dict | None) -> tuple[float, str]:
    """Total ms the engine spent inside tools before finishing, and which ones."""
    if not e:
        return 0.0, ""
    total, names = 0.0, []
    for k, v in e.items():
        m = re.match(r"^(r\d+_tool_.+)_start$", k)
        if m and (m.group(1) + "_end") in e:
            total += float(e[m.group(1) + "_end"]) - float(v)
            names.append(m.group(1).split("_tool_", 1)[1])
    return total, ",".join(names)


def kind_of(a: dict) -> str:
    launch = num(a, "since_launch_s")
    wake = num(a, "since_wake_s")
    if launch is not None and launch < 150:
        return "cold"
    if wake is not None and wake < 150:
        return "wake"
    return "warm"


def fmt(v):
    return "   -" if v is None else f"{v:5.0f}"


def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="0")
    ap.add_argument("--log", default=str(Path.home() / "Misty/logs/voice.log"))
    args = ap.parse_args()
    app, eng, starts, wakes = parse(Path(args.log), args.since)
    if not app:
        print("no TURN_TIMING lines yet (talk to Misty with the hotkey first)")
        return 1
    rows = []
    for turn, a in app.items():
        s = slices(a, eng.get(turn))
        total = (first(a, "first_chunk") or 0) + (num(a, "out_latency") or 0)
        tm, tn = tool_ms(eng.get(turn))
        a = {**a, "_tool_ms": str(tm), "_tools": tn}
        rows.append((turn, kind_of(a), s, total, a))
    names = [k for k, _ in SLICES]
    print("ms from the tap that ends his speech to the sound leaving the speaker\n")
    print("turn      kind  speech " + " ".join(f"{n[:6]:>6}" for n in names) + "  TOTAL  1st-clause")
    for turn, kind, s, total, a in rows:
        print(f"{turn[:9]:<9} {kind:<5} {num(a, 'speech_s') or 0:5.1f}s " + " ".join(fmt(s[n]) + " " for n in names)
              + f" {total:6.0f}  {int(num(a, 'first_clause_chars') or 0):>3}ch  tools={num(a, '_tool_ms') or 0:.0f}ms {a['_tools']}")
    print()
    groups = [("warm", lambda r: r[1] == "warm"), ("cold", lambda r: r[1] == "cold"), ("wake", lambda r: r[1] == "wake"),
              ("no tool", lambda r: not r[4]["_tools"]), ("tool", lambda r: bool(r[4]["_tools"])), ("all", lambda r: True)]
    for label, f in groups:
        sel = [r for r in rows if f(r)]
        if not sel:
            continue
        t = [r[3] for r in sel]
        print(f"{label:>8}: {len(sel):2d} turns   total median {statistics.median(t):5.0f}  min {min(t):5.0f}  max {max(t):5.0f}")
    nt = [r for r in rows if not r[4]["_tools"]]
    print("\nMedian per slice (turns with no tool; the tool turns are shown separately above):" if nt else "\nMedian per slice (all turns):")
    allr = nt or rows
    tot_med = statistics.median([r[3] for r in allr])
    ranked = []
    for k, desc in SLICES:
        m = med([r[2][k] for r in allr])
        ranked.append((m or 0, k, desc))
    for m, k, desc in sorted(ranked, reverse=True):
        bar = "#" * int(40 * m / tot_med) if tot_med else ""
        print(f"  {m:6.0f} ms  {desc:<40} {bar}")
    if starts:
        print("\nTap to 'listening' (mic open):")
        for stamp, d in starts:
            print(f"  {stamp}  listen_ms={d.get('listen_ms')}  ears_ready={d.get('ears_ready')}  "
                  f"since_launch_s={d.get('since_launch_s')}  since_wake_s={d.get('since_wake_s')}")
    if wakes:
        print("\nWake events seen:", ", ".join(wakes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
