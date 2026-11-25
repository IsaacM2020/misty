#!/usr/bin/env python3
"""F0: where the time goes in a voice turn, split cold / wake / warm and plain / tool, plus why the wall's "first word" row is red.
Free: reads ~/Misty/logs/voice.log (parsed by scripts/turn_timing_report.py, imported, not copied) and engine.log.

    engine/.venv/bin/python scripts/checks/talk_baseline.py [--last 60] [--write] [--rebaseline]
    engine/.venv/bin/python scripts/checks/talk_baseline.py --typed 30 --max-usd 0.04     # PAID, pending: typed plain turns, engine slices only

Per slice: median and 90th percentile. `cached_pct` is the share of the turn's prompt tokens the provider served from cache: the turn's
OR_CALL line in engine.log has no turn id, so it is joined by time (a chat call with no job id, inside the turn's own window) and is
"unknown" when no call matches. The 650 ms of quiet that ends his speech sits inside his speech, not inside TOTAL.
`--write` appends the table to baselines.md under a dated heading. The `first_word_median_ms:` line is rewritten only with `--rebaseline`
(Isaac's yes first). The wall limit is never moved here.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import turn_timing_report as ttr  # noqa: E402

LOG = Path.home() / "Misty" / "logs" / "voice.log"
ENGINE_LOG = Path.home() / "Misty" / "logs" / "engine.log"
BASE = Path.home() / "Notes" / "projects" / "misty" / "baselines.md"
SLICE_NAMES = [k for k, _ in ttr.SLICES]
OR = re.compile(r"^(\S+) INFO OR_CALL (.*)$")
TAP_TO_FIRST_WORD = "TOTAL"
MIN_TURNS = 10
WINDOW = 30
SLACK_S = 2.0


def _epoch(stamp: str) -> float | None:
    try:
        return time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except ValueError:
        return None


def or_calls(engine_log: Path) -> list[tuple[float, int, int]]:
    """(epoch, tokens_in, cached) of every chat-model call that no background job made."""
    out = []
    try:
        text = engine_log.read_text(errors="replace")
    except OSError:
        return out
    for raw in text.splitlines():
        m = OR.match(raw)
        if not m:
            continue
        d = ttr.kv(m.group(2))
        if d.get("job_id") not in (None, "None") or "haiku" not in d.get("model", "") or "tokens_in" not in d:
            continue
        ep = _epoch(m.group(1))
        if ep is not None:
            out.append((ep, int(d["tokens_in"]), int(d.get("cached") or 0)))
    return out


def cached_for(e: dict | None, calls: list[tuple[float, int, int]]) -> float | None:
    """Cache share of the turn's first model call, or None. Window: from the request being sent to the end of the turn."""
    if not e or not calls:
        return None
    t0, start, end = ttr.num(e, "t0"), ttr.num(e, "r0_http_start"), ttr.num(e, "end")
    if t0 is None or start is None or end is None:
        return None
    lo, hi = t0 + start / 1000 - SLACK_S, t0 + end / 1000 + SLACK_S
    hit = [c for c in calls if lo <= c[0] <= hi]
    if not hit or hit[0][1] <= 0:
        return None
    return round(100.0 * hit[0][2] / hit[0][1], 1)


def load(voice_log: Path, engine_log: Path = ENGINE_LOG, since: str = "0") -> list[dict]:
    try:
        app, eng, _, _ = ttr.parse(voice_log, since)
    except OSError:
        return []
    calls = or_calls(engine_log)
    turns = []
    for tid, a in app.items():
        e = eng.get(tid)
        s = ttr.slices(a, e)
        tool_ms, tools = ttr.tool_ms(e)
        total = (ttr.first(a, "first_chunk") or 0) + (ttr.num(a, "out_latency") or 0)
        turns.append({"turn": tid, "kind": ttr.kind_of(a), "tool": bool(tools), "tools": tools, "tool_ms": tool_ms, "slices": s,
                      "total": total, "speech_s": ttr.num(a, "speech_s") or 0.0, "cached_pct": cached_for(e, calls)})
    return turns


def group(turns: list[dict]) -> dict[tuple[str, str], list[dict]]:
    g: dict[tuple[str, str], list[dict]] = {}
    for t in turns:
        g.setdefault((t["kind"], "tool" if t["tool"] else "plain"), []).append(t)
    return g


def stats(vals: list) -> dict:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return {"n": 0, "median": None, "p90": None}
    p90 = v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))]
    return {"n": len(v), "median": statistics.median(v), "p90": p90}


def _f(x) -> str:
    return "    -" if x is None else f"{x:5.0f}"


def _table(label: str, ts: list[dict]) -> list[str]:
    rows = [f"{label}: {len(ts)} turns"]
    rows.append(f"  {'slice':<11} {'median':>6} {'p90':>6}")
    for name in SLICE_NAMES:
        s = stats([t["slices"][name] for t in ts])
        rows.append(f"  {name:<11} {_f(s['median'])}  {_f(s['p90'])}")
    s = stats([t["total"] for t in ts])
    rows.append(f"  {TAP_TO_FIRST_WORD:<11} {_f(s['median'])}  {_f(s['p90'])}")
    c = [t["cached_pct"] for t in ts if t["cached_pct"] is not None]
    rows.append(f"  cached_pct  {statistics.median(c):.0f} (median of {len(c)} joined)" if c else "  cached_pct  unknown (no OR_CALL line joined)")
    return rows


def verdict(turns: list[dict], limit: float, window: int = WINDOW) -> dict:
    """Why "first word" is red: the last 30 plain turns, cold and wake against warm."""
    plain = [t for t in turns if not t["tool"]][-window:]
    out = {"n": len(plain), "limit": limit, "all_median": None, "warm_median": None, "cold_median": None, "cold_n": 0, "cause": "unknown", "text": ""}
    if len(plain) < MIN_TURNS:
        out["text"] = f"only {len(plain)} plain turns, no verdict (needs {MIN_TURNS})"
        return out
    warm = [t["total"] for t in plain if t["kind"] == "warm"]
    cold = [t["total"] for t in plain if t["kind"] != "warm"]
    out["all_median"] = statistics.median(t["total"] for t in plain)
    out["cold_n"] = len(cold)
    out["cold_median"] = statistics.median(cold) if cold else None
    if not warm:
        out["text"] = "no warm turn among them, cannot compare"
        return out
    out["warm_median"] = statistics.median(warm)
    if out["all_median"] <= limit:
        out["cause"] = "none"
        out["text"] = f"median {out['all_median']:.0f} ms is inside the limit {limit:.0f} ms"
    elif out["warm_median"] <= limit:
        out["cause"] = "cold"
        out["text"] = (f"cold and wake turns ({len(cold)} of {len(plain)}, median {out['cold_median']:.0f} ms) pull the median to {out['all_median']:.0f} ms; "
                       f"warm alone is {out['warm_median']:.0f} ms, under {limit:.0f}. The fix is F1 (warm the cache).")
    else:
        out["cause"] = "prep"
        out["text"] = (f"warm turns alone are {out['warm_median']:.0f} ms, over {limit:.0f}, so it is not only cold starts: prompt and prep growth. "
                       f"The fix is F1 part 1b (trim prep).")
    return out


WARM = re.compile(r"^(\S+) INFO WARM (.*)$")


def warm_summary(engine_log: Path) -> str:
    """F1: how many warms were sent or skipped (and why) per day, and what they cost. Empty when the log has none."""
    days: dict[str, dict] = {}
    try:
        text = engine_log.read_text(errors="ignore")
    except OSError:
        return ""
    for line in text.splitlines():
        m = WARM.match(line)
        if not m:
            continue
        f = dict(kv.split("=", 1) for kv in m.group(2).split() if "=" in kv)
        d = days.setdefault(m.group(1)[:10], {"sent": 0, "skip": {}, "usd": 0.0, "cached": []})
        if f.get("kind") == "sent":
            d["sent"] += 1
            d["usd"] += float(f.get("usd", 0) or 0)
            d["cached"].append(int(f.get("cached", 0) or 0))
        else:
            why = f.get("why", "?")
            d["skip"][why] = d["skip"].get(why, 0) + 1
    if not days:
        return ""
    out = ["Warms (F1), per day: sent, skipped by reason, cost, median cached tokens seen by the warm itself (0 = it really was cold)"]
    for day in sorted(days):
        d = days[day]
        skips = ", ".join(f"{k} {v}" for k, v in sorted(d["skip"].items())) or "none"
        med = statistics.median(d["cached"]) if d["cached"] else "-"
        out.append(f"  {day}: sent {d['sent']}, skipped {skips}, cost ${d['usd']:.4f}, warm cached median {med}")
    return "\n".join(out)


def early_summary(voice_log: Path) -> str:
    """F2: how many phrases ended early (350 ms of quiet) and how many of those were merged by a later phrase. Empty when none."""
    try:
        text = voice_log.read_text(errors="ignore")
    except OSError:
        return ""
    early = len(re.findall(r"EARLY_END quiet_ms=", text))
    merged = len(re.findall(r"EARLY_END merged=true", text))
    if not early and not merged:
        return ""
    pct = f"{100 * merged / early:.0f}" if early else "n/a"
    return (f"Early ends (F2): {early} phrases ended at 350 ms, {merged} of them merged by a later phrase ({pct} percent). "
            f"The bar: at most 1 cut-off in 30 spoken turns.")


def report(turns: list[dict], limit: float, warm: str = "") -> str:
    g = group(turns)
    out = ["ms from the tap that ends his speech to the sound leaving the speaker (TOTAL), and each slice's median and 90th percentile", ""]
    for kind in ("cold", "wake", "warm"):
        for what in ("plain", "tool"):
            ts = g.get((kind, what))
            if ts:
                out += _table(f"{kind} {what}", ts) + [""]
    for what in ("plain", "tool"):
        ts = [t for t in turns if (t["tool"] and what == "tool") or (not t["tool"] and what == "plain")]
        if ts:
            out += _table(f"all {what}", ts) + [""]
    v = verdict(turns, limit)
    out.append(f"Note (endpoint wait): the 650 ms of quiet that ends his speech (class: 600) is inside his speech, not inside TOTAL, so a turn feels about that much slower than TOTAL.")
    out.append(f"Verdict (last {v['n']} plain turns, limit {limit:.0f} ms): {v['text']}")
    if warm:
        out += ["", warm]
    return "\n".join(out)


def limit_from(base: Path) -> float | None:
    m = re.search(r"first_word_median_ms:\s*(\d+)", base.read_text()) if base.exists() else None
    return float(m.group(1)) * 1.10 if m else None


def write(base: Path, turns: list[dict], limit: float, rebaseline: bool = False, today: str | None = None, warm_text: str = "") -> None:
    today = today or time.strftime("%Y-%m-%d")
    text = base.read_text() if base.exists() else ""
    text = text.rstrip("\n") + f"\n\n## Re-measure ({today})\n\n```\n{report(turns, limit, warm_text)}\n```\n"
    if rebaseline:
        plain = [t["total"] for t in turns if not t["tool"]][-WINDOW:]
        if plain:
            new = f"first_word_median_ms: {statistics.median(plain):.0f}"
            text = re.sub(r"first_word_median_ms:\s*\d+", new, text, count=1)
    base.write_text(text)


TYPED = ["hello there", "what is a tide", "tell me a joke", "how are you today", "what does a thermostat do", "say something kind",
         "what is a good name for a cat", "explain a rainbow in one line", "what is two plus two", "how do bees make honey"]


async def typed_run(ctx, n: int, phrases: list[str] | None = None, max_usd: float = 0.04) -> list[dict]:
    """PAID when ctx has a real model: n plain typed turns through chat.send as voice turns, engine marks only."""
    from misty_engine import chat
    from misty_engine.openrouter import user_approved
    from misty_engine.turn_timing import Marks

    async def nothing(_):
        return None

    async def no(_):
        return False
    rows, spent = [], 0.0
    phrases = phrases or TYPED
    for i in range(n):
        if spent >= max_usd:
            break
        marks = Marks()
        with user_approved():
            out = await chat.send(ctx, phrases[i % len(phrases)], on_delta=nothing, on_status=nothing, confirm=no, voice=True, marks=marks)
        spent += float(out.get("usd") or 0.0)
        rows.append(dict(marks.items))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(LOG))
    ap.add_argument("--engine-log", default=str(ENGINE_LOG))
    ap.add_argument("--baseline", default=str(BASE))
    ap.add_argument("--since", default="0")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--rebaseline", action="store_true")
    ap.add_argument("--typed", type=int, default=0)
    ap.add_argument("--max-usd", type=float, default=0.04)
    a = ap.parse_args(argv)
    if a.typed:
        import _harness
        ctx, home = _harness.temp_ctx(copy_live=False)
        try:
            rows = asyncio.run(typed_run(ctx, a.typed, None, a.max_usd))
        finally:
            _harness.cleanup(home)
        cols = ["recv", "frame_ready", "prompt_built", "prompt_ready", "tools_built", "user_saved", "preflight_done", "r0_post", "r0_http_start", "r0_headers"]
        print("typed turns, engine marks in ms from receipt: median over", len(rows))
        for c in cols:
            vals = [r[c] for r in rows if c in r]
            print(f"  {c:<15} {statistics.median(vals) if vals else '-'}")
        return 0
    base = Path(a.baseline)
    limit = limit_from(base)
    turns = load(Path(a.log), Path(a.engine_log), a.since)
    if not turns:
        print("no TURN_TIMING lines yet (talk to Misty with the hotkey first)")
        return 1
    lim = limit if limit is not None else 0.0
    extra = "\n".join(x for x in (warm_summary(Path(a.engine_log)), early_summary(Path(a.log))) if x)
    print(report(turns, lim, extra))
    if a.write:
        write(base, turns, lim, a.rebaseline, warm_text=extra)
        print(f"\nappended to {base}" + (" and rewrote first_word_median_ms" if a.rebaseline else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
