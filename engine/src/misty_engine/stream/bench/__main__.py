"""stream-bench: run practice tasks through the running Misty app's engine and score them.

  cd engine && uv run python -m misty_engine.stream.bench --brain haiku --tasks notes_new,google_search
  cd engine && uv run python -m misty_engine.stream.bench --brain qwen --task "free text task"   (Isaac's surprise tasks)
Total spend across all bench runs is capped (Isaac approved $3 on 2026-10-21, including $0.081 from the spike).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

from .tasks import BY_NAME, TASKS, Task

TOTAL_CAP = 3.00
HOME = Path(os.environ.get("MISTY_HOME", Path.home() / "Misty"))
LEDGER = HOME / "stream" / "bench-spend.json"
OUT = HOME / "stream" / "bench"


def spent() -> float:
    try:
        return json.loads(LEDGER.read_text())["usd"]
    except Exception:
        return 0.081147                                   # carried over from the cu-spike test


def add(usd: float) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps({"usd": round(spent() + usd, 6)}))


async def rpc(method: str, params: dict, timeout: float = 400) -> dict:
    r, w = await asyncio.open_unix_connection(str(HOME / "run" / "engine.sock"), limit=32 * 1024 * 1024)
    w.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode() + b"\n")
    await w.drain()
    while True:
        line = await asyncio.wait_for(r.readline(), timeout)
        msg = json.loads(line)
        if msg.get("id") == 1:
            w.close()
            if "error" in msg:
                raise RuntimeError(msg["error"])
            return msg["result"]


async def one(task: Task, brain: str, tag: str, outdir: Path, max_usd: float) -> dict:
    task.setup(tag)
    text = task.text.format(tag=tag)
    print(f"\n== {brain} {task.name}: {text}", flush=True)
    try:
        r = await rpc("stream.run", {"task": text, "brain": brain, "app": task.app, "max_usd": max_usd, "confirm": "deny"})
    except Exception as e:
        r = {"outcome": f"rpc_error:{e}", "summary": "", "steps": 0, "raw_steps": 0, "secs": 0, "usd": 0.0,
             "asked": [], "run_id": "", "trace": []}
    add(r["usd"])
    shot = outdir / f"{brain}-{task.name}.png"
    subprocess.run(["screencapture", "-x", str(shot)], check=False)
    try:
        ok = task.check(r, tag)
    except Exception as e:
        ok = f"check failed: {e}"
    row = {"task": task.name, "brain": brain, "verified": ok, "outcome": r["outcome"], "summary": r["summary"],
           "steps": r["steps"], "raw_steps": r["raw_steps"], "secs": r["secs"], "usd": round(r["usd"], 5),
           "asked": r["asked"], "run_id": r["run_id"], "trace": r["trace"], "shot": str(shot)}
    print(f"  -> {r['outcome']} verified={ok} steps={r['steps']} raw={r['raw_steps']} {r['secs']}s ${r['usd']:.4f}", flush=True)
    return row


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default="haiku")
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--task", help="a free-text task (judged by Isaac)")
    ap.add_argument("--max-usd", type=float, default=0.10)
    a = ap.parse_args()
    tag = time.strftime("%H%M")
    outdir = OUT / time.strftime("%Y%m%d-%H%M%S")
    outdir.mkdir(parents=True)
    todo = [Task("surprise", a.task, lambda r, t: None)] if a.task else (
        TASKS if a.tasks == "all" else [BY_NAME[n] for n in a.tasks.split(",")])
    rows = []
    for brain in a.brain.split(","):
        for t in todo:
            if spent() + a.max_usd > TOTAL_CAP:
                print(f"total cap ${TOTAL_CAP} would be passed (spent ${spent():.3f}), stopping")
                break
            rows.append(await one(t, brain, tag, outdir, a.max_usd))
            (outdir / "results.json").write_text(json.dumps(rows, indent=1))
    print(f"\nsaved {outdir}  total bench spend ${spent():.4f} of ${TOTAL_CAP}")
    for b in a.brain.split(","):
        mine = [r for r in rows if r["brain"] == b]
        good = sum(r["verified"] is True for r in mine)
        unk = sum(r["verified"] is None for r in mine)
        print(f"{b}: {good}/{len(mine)} verified, {unk} to judge by screenshot, ${sum(r['usd'] for r in mine):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
