#!/usr/bin/env python3
"""Tool-pick check (the ruler, R0): for each thing Isaac might say, which tool does the real chat model reach for?

    engine/.venv/bin/python scripts/checks/toolpick.py [--runs 3] [--only TAG] [--set FILE ...] [--min 0.95] [--no-live]

Reads YAML lists of {say, expect_tool, expect_args_contains?, live?, tag?, needs?}.
  needs: lets_go|answer_ask  the tool is only sent in its state (R1), so the check puts that state up for this one line.
  expect_tool: none     a plain reply, no tool.
  expect_tool: [a, b]   either answer is right (only Isaac adds a second answer).
  live: true            only makes sense with his real data. Runs on a copy of his live db as a SECOND number, printed and
                        NOT part of the bar. Only Isaac marks a line live.
  tag: x                a line also belongs to tag x. Lines under a `# B2: ...` or `# Step C4: ...` comment get that tag too.
The main set runs on an empty db seeded with tests/engine/fixtures/toolpick.state.yaml (a made-up stranger), never on his data.
Same `say` and same `expect_tool` count once. Runs the whole set --runs times (default 3) and prints the mean, every miss with
how often it missed (1/3, 2/3, 3/3) grouped "always wrong" and "flaky", and the harm count: a miss is harmful when she called a
write tool that was not wanted. One model call per phrasing, stops at the first tool call. No tool ever runs. Paid.
Exit 1 when the mean is under --min or a line is harmful in every run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

import yaml  # noqa: E402

FIX = _harness.ROOT / "tests" / "engine" / "fixtures"
SAMPLE = FIX / "toolpick.sample.yaml"
STATE = FIX / "toolpick.state.yaml"
HIS = Path.home() / "Misty" / "checks" / "toolpick.yaml"

# Tools that change something (his data, his settings, his Mac). A miss that calls one of these is harmful.
WRITE = {"remember", "follow", "care", "way", "lesson", "setting", "speak_up", "move", "do", "protocol", "course", "teach"}
READ = {"work_on", "lets_go", "feedback", "answer_ask", "open_fact", "recall", "search", "read_page", "class_notes",
        "class_search", "calendar", "look", "read_screen", "solve", "judge", "draw", "slate", "mail", "costs",
        "recent_files", "calc"}

_TAG = re.compile(r"^#\s*(?:Step\s+)?([A-Za-z]\d+[a-z]?)\b")


def _expected(row: dict) -> list[str]:
    e = row["expect_tool"]
    return [str(x) for x in e] if isinstance(e, (list, tuple)) else [str(e)]


def _tags_by_item(text: str, n: int) -> list[set[str]]:
    """The tag from the nearest `# B2:` or `# Step C4:` comment above each `- ` item (empty when none)."""
    cur, out = "", []
    for line in text.splitlines():
        m = _TAG.match(line.strip())
        if m:
            cur = m.group(1).lower()
        elif line.startswith("- "):
            out.append({cur} if cur else set())
    return out if len(out) == n else [set() for _ in range(n)]


def dedupe(rows: list[dict]) -> list[dict]:
    seen, out = {}, []
    for r in rows:
        k = (str(r["say"]), tuple(sorted(_expected(r))))
        if k not in seen:
            seen[k] = r
            out.append(r)
        elif r.get("_tags"):                               # same line from another file: keep it once, with both sets of tags
            seen[k]["_tags"] = set(seen[k].get("_tags", set())) | set(r["_tags"])
    return out


def load(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        if not p.exists():
            print(f"(no set at {p}, skipped)", file=sys.stderr)
            continue
        text = p.read_text()
        items = yaml.safe_load(text) or []
        for r, tags in zip(items, _tags_by_item(text, len(items))):
            extra = r.get("tag") or []
            extra = {str(t).lower() for t in ([extra] if isinstance(extra, str) else extra)}
            rows.append({**r, "_src": p.name, "_tags": tags | extra})
    return dedupe(rows)


def split_live(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    return [r for r in rows if not r.get("live")], [r for r in rows if r.get("live")]


def only(rows: list[dict], tag: str) -> list[dict]:
    t = tag.lower()
    return [r for r in rows if t in r.get("_tags", set())]


def right(row: dict, name: str, args: dict) -> bool:
    if name not in _expected(row):
        return False
    need = row.get("expect_args_contains")
    return not need or str(need).lower() in json.dumps(args).lower()


def harmful(row: dict, name: str) -> bool:
    """She called a write tool that the line did not want."""
    return name in WRITE and name not in _expected(row)


def seed(ctx, path: Path = STATE) -> None:
    """Fill an empty db from the state file: the same made-up state every run."""
    from misty_engine import cares, facts, follows, ways
    spec = yaml.safe_load(path.read_text()) or {}
    if spec.get("card"):                                  # the real "about him" card is a file about the real person: never used here
        card = str(spec["card"]).strip()
        ctx.card = lambda: card
    for k, v in (spec.get("settings") or {}).items():
        ctx.settings.set(k, v)
    for f in spec.get("facts") or []:
        facts.remember(ctx, f["key"], f["text"], f.get("link"), f.get("link_title"))
    for f in spec.get("follows") or []:
        follows.add(ctx, f["text"], f.get("source", "web"), f.get("query", ""), f.get("url"), f.get("every_min", 30))
    for c in spec.get("cares") or []:
        cares.set_care(ctx, c["key"], c["text"], c.get("kind", "other"))
    for w in spec.get("ways") or []:
        ways.set_way(ctx, w["key"], w["text"], w.get("kind", "other"), w.get("value", ""))
    now = ctx.clock.now()
    with ctx.con:
        for i, t in enumerate(spec.get("told") or []):
            ctx.con.execute(
                "INSERT INTO told(id, at, topic, topic_text, key, text, said, source_name, how, urgency) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"tl_seed{i}", now - 900 - i, t.get("topic", "seed"), t.get("topic", ""), t.get("key", f"seed{i}"),
                 t["said"], t["said"], t.get("source_name"), t.get("how", "say"), t.get("urgency", "normal")))


async def pick(ctx, say: str) -> tuple[str, dict, float]:
    """(tool name or 'none', its args, dollars) for one phrasing. Nothing is run."""
    from misty_engine import chat
    from misty_engine.openrouter import user_approved
    m = ctx.pinned.models
    body = {"model": m.haiku, "messages": chat.messages(ctx, say), "max_tokens": 400, "temperature": 0.3,
            "provider": {"order": list(m.haiku_provider_order), "allow_fallbacks": True},
            "reasoning": dict(m.haiku_reasoning), "tools": chat.tools_for(ctx), "tool_choice": "auto"}
    with user_approved():
        data = await ctx.openrouter.post(ctx.pinned.openrouter.chat_path, body, job_id=None, phase="live",
                                         build_id=None, estimate_usd=0.01, model=m.haiku, purpose="chat")
    usd = float((data.get("usage") or {}).get("cost") or 0.0)
    calls = data["choices"][0]["message"].get("tool_calls") or []
    if not calls:
        return "none", {}, usd
    fn = calls[0].get("function") or {}
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    return fn.get("name", ""), args if isinstance(args, dict) else {}, usd


def score(rows: list[dict], runs: list[list[tuple[str, dict]]]) -> dict:
    """runs[i][j] = (tool, args) that run i gave for rows[j]."""
    n = len(runs)
    per_run = [sum(right(r, *res[j]) for j, r in enumerate(rows)) for res in runs]
    lines, always, flaky, harm_always = [], [], [], []
    harm_count = harm_lines = 0
    for j, r in enumerate(rows):
        missed = sum(not right(r, *res[j]) for res in runs)
        hurt = sum(harmful(r, res[j][0]) for res in runs)
        got = sorted({res[j][0] for res in runs if not right(r, *res[j])})
        item = {"say": r["say"], "want": "/".join(_expected(r)), "missed": missed, "runs": n, "got": got, "harm": hurt,
                "src": r.get("_src", "")}
        lines.append(item)
        harm_count += hurt
        harm_lines += bool(hurt)
        if missed == n:
            always.append(item)
        elif missed:
            flaky.append(item)
        if hurt == n:
            harm_always.append(item)
    return {"total": len(rows), "runs": n, "per_run": per_run, "mean": sum(per_run) / n if n else 0.0, "lines": lines,
            "always": always, "flaky": flaky, "harm_count": harm_count, "harm_lines": harm_lines, "harm_always": harm_always}


def passed(s: dict, min_mean: float) -> bool:
    return s["total"] > 0 and s["mean"] / s["total"] >= min_mean and not s["harm_always"]


def _miss_line(m: dict) -> str:
    h = "  HARM" if m["harm"] else ""
    return f"  {m['missed']}/{m['runs']}  \"{m['say']}\"  wanted {m['want']}, got {', '.join(m['got'])}{h}"


def report(s: dict, usd: float, runs: int, label: str = "tool pick") -> str:
    t = s["total"]
    out = [f"{label}: mean {s['mean']:.2f} of {t} ({s['mean'] / t:.1%}) over {runs} run{'s' if runs != 1 else ''}"
           f" [{', '.join(str(x) for x in s['per_run'])}], ${usd:.4f}"]
    out.append(f"always wrong ({len(s['always'])}):")
    out += [_miss_line(m) for m in s["always"]] or ["  none"]
    out.append(f"flaky ({len(s['flaky'])}):")
    out += [_miss_line(m) for m in s["flaky"]] or ["  none"]
    out.append(f"harm: {s['harm_count']} harmful call(s) on {s['harm_lines']} line(s); "
               f"{len(s['harm_always'])} harmful in every run")
    return "\n".join(out)


def arrange(ctx, need: str):
    """R1: judge, answer_ask and lets_go are only sent in their state. Put the state up for one line; returns the undo."""
    if need == "lets_go":
        before = ctx.extras
        ctx.extras = {**(before or {}), "openable": {"at": ctx.clock.now(), "items": [
            {"id": "u1", "kind": "link", "target": "https://example.com/task", "label": "the task page"}]}}

        def undo():
            ctx.extras = before
        return undo
    if need == "answer_ask":
        with ctx.con:
            ctx.con.execute(
                "INSERT INTO told(id, at, topic, topic_text, key, text, said, source_name, how, urgency) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("tl_need_ask", ctx.clock.now(), "chess", "the chess tournament", "need-ask", "The final is tonight.",
                 "I've got something on the chess tournament. Want to hear it?", "chess feed", "ask", "normal"))

        def undo():
            with ctx.con:
                ctx.con.execute("UPDATE told SET feedback='declined', feedback_at=at WHERE id='tl_need_ask'")
        return undo
    raise ValueError(f"no way to put up the state for needs: {need}")


async def run_set(ctx, rows: list[dict], runs: int) -> tuple[list[list[tuple[str, dict]]], float]:
    sem = asyncio.Semaphore(4)

    async def one(r):
        async with sem:
            try:
                name, args, cost = await pick(ctx, r["say"])
                return name, args, cost
            except Exception as e:                      # a failed call counts as a miss, never a crash
                return f"ERROR {type(e).__name__}", {}, 0.0

    async def one_with_state(r):                        # one at a time: the state belongs to this line only
        undo = arrange(ctx, r["needs"])
        try:
            return await one(r)
        finally:
            undo()
    out, usd = [], 0.0
    plain = [j for j, r in enumerate(rows) if not r.get("needs")]
    for _ in range(runs):
        res: list = [None] * len(rows)
        got = await asyncio.gather(*(one(rows[j]) for j in plain))
        for j, g in zip(plain, got):
            res[j] = g
        for j, r in enumerate(rows):
            if r.get("needs"):
                res[j] = await one_with_state(r)
        out.append([(n, a) for n, a, _ in res])
        usd += sum(c for _, _, c in res)
    return out, usd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", nargs="*", type=Path, help="YAML files (default: the sample and his own set)")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", help="run just the lines with this tag (a card's new lines, one run is enough)")
    ap.add_argument("--no-live", action="store_true", help="skip the live lines (they are printed, never part of the bar)")
    ap.add_argument("--dump", type=Path, help="write every line's result as JSON (for comparing runs)")
    ap.add_argument("--min", type=float, default=0.95)
    a = ap.parse_args()
    rows = load(a.set or [SAMPLE, HIS])
    if not a.set:                                              # R2: the moves' own lines are a third set (deduped against the files)
        from misty_engine.moves import pick_lines
        rows = dedupe(rows + pick_lines())
    if a.only:
        rows = only(rows, a.only)
    main_rows, live_rows = split_live(rows)
    if not main_rows and not live_rows:
        print("no phrasings found", file=sys.stderr)
        return 2
    results, usd_all = {}, 0.0
    if main_rows:
        ctx, home = _harness.temp_ctx(copy_live=False)
        try:
            seed(ctx)
            results["main"] = asyncio.run(run_set(ctx, main_rows, a.runs))
        finally:
            _harness.cleanup(home)
    if live_rows and not a.no_live:
        ctx, home = _harness.temp_ctx(copy_live=True)
        try:
            results["live"] = asyncio.run(run_set(ctx, live_rows, a.runs))
        finally:
            _harness.cleanup(home)
    code = 0
    if "main" in results:
        runs_, usd = results["main"]
        s = score(main_rows, runs_)
        usd_all += usd
        print(report(s, usd, a.runs))
        code = 0 if passed(s, a.min) else 1
        if a.dump:
            a.dump.write_text(json.dumps({"mean": s["mean"], "total": s["total"], "lines": s["lines"],
                                          "at": time.strftime("%Y-%m-%d %H:%M")}, indent=1))
    if "live" in results:
        runs_, usd = results["live"]
        usd_all += usd
        print("\n" + report(score(live_rows, runs_), usd, a.runs, "LIVE lines (his own data, not part of the bar)"))
    print(f"\ntotal ${usd_all:.4f}")
    return code


if __name__ == "__main__":
    sys.exit(main())
