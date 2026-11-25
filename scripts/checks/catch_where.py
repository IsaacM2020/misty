#!/usr/bin/env python3
"""G0: where does he really work, and can she read it? Free. Reads only app names, minutes and counts. Never titles or text.

    engine/.venv/bin/python scripts/checks/catch_where.py [--db <path>] [--days 14]
    engine/.venv/bin/python scripts/checks/catch_where.py --probe [--gap 10]

Ranking: the apps he spent most minutes in over the last 14 days, from the `activity` table. The column "reads text?" is left empty
until a probe fills it. Run it on a COPY of the live db.

--probe: with ONE of his working pages open and in front, it reads his window twice, `--gap` seconds apart (default 10), through the
running engine's hands. You type the marker line it prints into the page in between. It prints only: app, characters, how
(accessibility or ocr), lines, furniture (lines that are in both reads: page furniture, though his unchanged text counts too, so it is
an upper bound), and whether the marker was found. Then one grade: reads well | reads thin | cannot read | private.
reads well = marker found, by accessibility, and furniture at most 30% of the lines.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

DAYS = 14
TOP = 8
FURNITURE_MAX = 0.30


def rank(con, now: float | None = None, days: int = DAYS, top: int = TOP) -> dict:
    now = time.time() if now is None else now
    since = now - days * 86400
    con.row_factory = sqlite3.Row
    mins: dict[str, dict] = {}
    day_set, total = set(), 0.0
    for r in con.execute("SELECT at, until, bundle, app FROM activity WHERE until>? AND at<?", (since, now)):
        lo, hi = max(r["at"], since), min(r["until"], now)
        if hi <= lo:
            continue
        m = (hi - lo) / 60
        e = mins.setdefault(r["bundle"], {"app": r["app"], "bundle": r["bundle"], "minutes": 0.0})
        e["minutes"] += m
        total += m
        day_set.add(int(lo // 86400))
    apps = sorted(mins.values(), key=lambda e: -e["minutes"])[:top]
    for e in apps:
        e["minutes"] = round(e["minutes"])
        e["reads_text"] = ""
    return {"days": days, "apps": apps, "total_minutes": round(total), "days_with_data": len(day_set)}


def render(r: dict) -> str:
    out = [f"Where he works, last {r['days']} days: {r['total_minutes']} min in all ({r['total_minutes'] / 60:.1f} h), on {r['days_with_data']} day(s) with data.",
           "", f"{'app':<24}{'bundle':<36}{'min':>6}  reads text?"]
    for e in r["apps"]:
        out.append(f"{e['app'][:23]:<24}{e['bundle'][:35]:<36}{e['minutes']:>6}  {e['reads_text']}")
    return "\n".join(out)


# ---- the probe ------------------------------------------------------------------------------------------------------------------
def new_marker() -> str:
    return f"PROBE {random.randint(100, 9999)}"


def _lines(d: dict) -> list[str]:
    return [ln.strip() for ln in str(d.get("text") or "").splitlines() if ln.strip()]


def analyse(one: dict, two: dict, marker: str) -> dict:
    """Numbers only. The text is dropped here."""
    if one.get("private") or two.get("private"):
        return {"private": True}
    a, b = _lines(one), _lines(two)
    seen = set(a)
    furniture = sum(1 for ln in b if ln in seen and marker not in ln)
    return {"app": str(two.get("app") or one.get("app") or "")[:60], "chars": len(str(two.get("text") or "")), "how": str(two.get("how") or one.get("how") or "accessibility"),
            "lines": len(b), "furniture": furniture, "marker": any(marker in ln for ln in b)}


def grade(a: dict) -> str:
    if a.get("private"):
        return "private"
    if a.get("error") or not a.get("marker") or not a.get("chars"):
        return "cannot read"
    if a["how"] != "accessibility" or a["furniture"] > FURNITURE_MAX * max(a["lines"], 1):
        return "reads thin"
    return "reads well"


async def probe(read, sleep, marker: str, gap: float = 10) -> dict:
    try:
        one = await read()
        await sleep(gap)
        two = await read()
    except Exception as e:                                              # noqa: BLE001  a failed read is a result, not a crash
        return {"error": str(e)[:80]}
    return analyse(one, two, marker)


async def _engine_read():
    from misty_engine.cli import _rpc
    from misty_engine.paths import MistyPaths
    r = await _rpc(str(MistyPaths.from_env().sock), "hands.call", {"op": "read_window_text"})
    if "error" in r:
        raise RuntimeError(str(r["error"].get("message") or r["error"])[:80])
    return (r.get("result") or {}).get("data") or {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(Path.home() / "Misty" / "misty.db"))
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--gap", type=float, default=10)
    a = ap.parse_args()
    if not a.probe:
        con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
        print(render(rank(con, days=a.days)))
        return 0
    marker = new_marker()
    print(f"Open ONE working page, in front. After the first read, type this line into it within {a.gap:.0f} s:  {marker}", flush=True)

    async def sleep(s):
        print(f"first read done. TYPE NOW: {marker}", flush=True)
        await asyncio.sleep(s)

    res = asyncio.run(probe(_engine_read, sleep, marker, a.gap))
    res["grade"] = grade(res)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
