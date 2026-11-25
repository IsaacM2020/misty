#!/usr/bin/env python3
"""Card A3: is she ready after a reboot and a wake, and how fast does she go quiet when cut off.

Reads ~/Misty/logs/voice.log only (read-only, numbers only, nothing from the conversation).

    scripts/a3_report.py [--since 2026-11-15T06:00] [--log PATH]

Sections:
  launch     each app launch: seconds from boot to launch (if after the last boot), seconds until ears + voice
             are warm, and whether a turn answered after it
  wake       each Mac wake: engine answer time, the next tap's mic, whether she answered, any mic failure
  interrupt  each key cut-off: key_to_stop_ms + out_ms = silence ms, against the 300 ms bar (goal 100 ms)
  own lines  cut-offs of a line she started herself (UPDATE_INTERRUPT)
  talk-over  F4: he talked over a line she started herself (VOICE_TURN barge_stop_ms source=update), against 300 ms,
             and how many lines ended on their own (UPDATE_LINE end) against stops with nobody talking
  first word median ms from the end of his speech to her first sound, plain turns, from TURN_TIMING
"""
from __future__ import annotations

import argparse
import re
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path

LOG = Path.home() / "Misty/logs/voice.log"
KV = re.compile(r"(\w+)=(\S+)")


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def boot_time() -> datetime | None:
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
        sec = int(re.search(r"sec = (\d+)", out).group(1))
        return datetime.fromtimestamp(sec, timezone.utc)
    except Exception:
        return None


def local(t: datetime) -> str:
    return t.astimezone().strftime("%d %b %H:%M:%S")


def kv(rest: str) -> dict[str, str]:
    return dict(KV.findall(rest))


def read(path: Path, since: datetime | None) -> list[tuple[datetime, str]]:
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        head, _, rest = line.partition(" ")
        try:
            t = ts(head)
        except ValueError:
            continue
        if since and t < since:
            continue
        rows.append((t, rest))
    return rows


def answered_after(rows, i: int, until: datetime | None) -> bool:
    for t, r in rows[i + 1:]:
        if until and t >= until:
            return False
        if r.startswith("TURN_TIMING"):
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO time, local if no zone (e.g. 2026-11-15T14:00)")
    ap.add_argument("--log", default=str(LOG))
    a = ap.parse_args()
    since = None
    if a.since:
        since = datetime.fromisoformat(a.since)
        if since.tzinfo is None:
            since = since.astimezone()
    rows = read(Path(a.log), since)
    boot = boot_time()
    launches = [i for i, (_, r) in enumerate(rows) if r.startswith("summon key via")]
    wakes = [i for i, (_, r) in enumerate(rows) if r == "WAKE"]

    print(f"voice.log rows read: {len(rows)}" + (f" since {local(since)}" if since else ""))
    if boot:
        print(f"last boot: {local(boot)}")

    print("\n== launch ==")
    for n, i in enumerate(launches):
        t0 = rows[i][0]
        nxt = rows[launches[n + 1]][0] if n + 1 < len(launches) else None
        warm = next(((t, kv(r)) for t, r in rows[i + 1:] if r.startswith("warm ears=") and (not nxt or t < nxt)), None)
        ready = "never warmed"
        if warm:
            ms = int(warm[1].get("ears_ms", 0)) + int(warm[1].get("voice_ms", 0))
            ready = f"warm {ms / 1000:.1f} s after launch (ears {int(warm[1]['ears_ms']) / 1000:.1f} s)"
        after_boot = ""
        if boot and t0 >= boot and (n == 0 or rows[launches[n - 1]][0] < boot):
            after_boot = f"  <- first launch after boot, {(t0 - boot).total_seconds():.0f} s after boot"
        ok = "answered a turn" if answered_after(rows, i, nxt) else "no turn yet"
        print(f"{local(t0)}  {ready}, {ok}{after_boot}")

    print("\n== wake ==")
    for n, i in enumerate(wakes):
        t0 = rows[i][0]
        nxt = rows[wakes[n + 1]][0] if n + 1 < len(wakes) else None
        eng, talk, fail, ans = "?", None, [], False
        for t, r in rows[i + 1:]:
            if nxt and t >= nxt:
                break
            if r.startswith("WAKE_READY") and eng == "?":
                d = kv(r)
                eng = f"{d.get('engine_ms')} ms ({d.get('state')})"
            elif r.startswith("TALK_START") and talk is None:
                d = kv(r)
                talk = f"mic open {d.get('listen_ms')} ms after tap, ears_ready={d.get('ears_ready')}, {(t - t0).total_seconds():.0f} s after wake"
            elif "mic start failed" in r or "never opened" in r or r.startswith("fail "):
                fail.append(r[:80])
            elif r.startswith("TURN_TIMING") and talk:
                ans = True
                break
        verdict = "PASS" if talk and ans and not fail else "check"
        print(f"{local(t0)}  engine {eng}; {talk or 'no tap after this wake'}; {'answered' if ans else 'no answer'}"
              f"{'; FAIL: ' + ' | '.join(fail) if fail else ''}  [{verdict}]")
    if not wakes:
        print("(no wakes)")

    def cuts(prefix: str):
        out = []
        for t, r in rows:
            if r.startswith(prefix):
                d = kv(r)
                if d.get("key_to_stop_ms", "none") != "none":
                    out.append((t, int(d["key_to_stop_ms"]), int(d.get("out_ms", 0)), r.split()[2] if prefix == "talk: interrupted" else "own line"))
        return out

    for title, prefix in (("interrupt (her answers)", "talk: interrupted"), ("own lines (she spoke up)", "UPDATE_INTERRUPT")):
        print(f"\n== {title} ==")
        c = cuts(prefix)
        for t, k, o, st in c:
            s = k + o
            print(f"{local(t)}  {st:9s} key->stop {k:4d} ms + out {o:3d} ms = silent in {s:4d} ms  {'ok' if s <= 300 else 'SLOW'}")
        if c:
            sil = [k + o for _, k, o, _ in c]
            print(f"-> {sum(s <= 300 for s in sil)} of {len(sil)} within 300 ms, median {statistics.median(sil):.0f} ms "
                  f"(goal 100 ms: {sum(s <= 100 for s in sil)} of {len(sil)})")
        else:
            print("(none with key timing yet)")

    print("\n== talk over her own lines (F4) ==")
    over = []
    for t, r in rows:
        if r.startswith("VOICE_TURN barge_stop_ms=") and "source=update" in r:
            d = kv(r)
            over.append((t, int(d["barge_stop_ms"]), int(d.get("out_ms", 0))))
    ended = sum(1 for _, r in rows if r.startswith("UPDATE_LINE end"))
    for t, b, o in over:
        print(f"{local(t)}  heard->stop {b:4d} ms + out {o:3d} ms = silent in {b + o:4d} ms  {'ok' if b + o <= 300 else 'SLOW'}")
    if over:
        print(f"-> {sum(b + o <= 300 for _, b, o in over)} of {len(over)} within 300 ms (bar: 9 of 10)")
    print(f"lines that ended on their own: {ended}; stopped by talking over: {len(over)} "
          "(with nobody talking, every stop is a false stop: the bar is 0)")

    print("\n== first word (plain turns) ==")
    first: dict[str, int] = {}
    for _, r in rows:
        if r.startswith("TURN_TIMING"):
            d = kv(r)
            if "first_chunk" in d and "turn" in d:
                first[d["turn"]] = int(d["first_chunk"])
    if first:
        v = sorted(first.values())
        print(f"{len(v)} turns, median {statistics.median(v) / 1000:.2f} s (includes tool turns; turn_timing_report.py splits plain)")
    else:
        print("(no turns)")


if __name__ == "__main__":
    main()
