#!/usr/bin/env python3
"""E5 scored check: the lesson plan. PAID (Haiku with thinking), stops at --max-usd.

    engine/.venv/bin/python scripts/checks/plan_check.py [--max-usd 0.10] [--only N] [--model haiku|strong] [--dump 3]

Fixture: tests/engine/fixtures/plan_set.json, 10 topics in 3 school-or-not groups (physics x3, chemistry x3, and four that are not school:
git, sourdough, running, bike repair), each with a short passage of 3 paragraphs.
Bars: usable at least 8 of 10; in usable plans every cite real (100%, code, rechecked here) and the three ways on every step (100%);
cost per plan under $0.05. `--dump N` prints N usable plans in plain text for Isaac to read (his call, written down, not pass or fail).
Runs on an empty throwaway data folder with its own spend meter. If the money stops first, the rest is "paid, pending".
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

FIX = _harness.ROOT / "tests" / "engine" / "fixtures" / "plan_set.json"


def first_quote(text: str) -> str:
    sent = text.split(". ")[0].strip()
    words = sent.split()
    return " ".join(words[:18])


def show(item, p) -> str:
    out = [f"=== {item['subject_title']}: {item['topic']} (plan v{p['version']}, ${p['cost']:.4f})"]
    for i, st in enumerate(p["plan"]["steps"], 1):
        out.append(f"-- step {i}: {st['idea']}")
        for w in st["explain"]:
            b = " [board]" if w["board"] else ""
            out.append(f"   {w['way']:8}{b} {w['say']}")
        c = st["check"]
        out.append(f"   check: {c['q']}  key={json.dumps(c['key'])}" if c else "   (explain only)")
    g, s = p["plan"]["guided"], p["plan"]["solo"]
    out += [f"-- guided: {g['q']}  key={json.dumps(g['key'])}", f"-- solo: {s['q']}  key={json.dumps(s['key'])}", f"-- recap: {p['plan']['recap']}"]
    return "\n".join(out)


async def main_async(a) -> int:
    from misty_engine.openrouter import user_approved
    from misty_engine.teach import ask, plan, sources
    from misty_engine.teach import map as tm
    ctx, home = _harness.temp_ctx(copy_live=False)
    items = json.loads(FIX.read_text())
    if a.only:
        items = items[:a.only]
    spent = {"usd": 0.0}
    orig = ask.think

    async def metered(ctx_, *args, **kw):
        if spent["usd"] >= a.max_usd:
            from misty_engine.errors import MistyError
            raise MistyError("budget reached")
        text, usd = await orig(ctx_, *args, **kw)
        spent["usd"] += usd
        return text, usd
    ask.think = metered
    usable, rejected, pending, costs, reasons = [], [], [], [], []
    cites_total = cites_real = steps_total = steps_three = 0
    shown = 0
    try:
        with user_approved():
            for it in items:
                if spent["usd"] >= a.max_usd:
                    pending.append(it["id"])
                    continue
                src = sources._keep(ctx, "page", f"https://fixture.test/{it['id']}", it["id"], "\n\n".join(it["paras"]))
                cs = sources.chunks(ctx, src["id"])
                q = first_quote(it["paras"][0])
                no = next(n for n, t in cs if sources.find_quote(ctx, src["id"], n, q))
                sid = tm.add_subject(ctx, it["subject"], it["subject_title"])["id"]
                tid = tm.add_topic(ctx, sid, it["id"], it["topic"], {"source_id": src["id"], "chunk_no": no, "quote": q})["id"]
                r = await plan.make(ctx, tid, model_key=a.model)
                costs.append(r["cost"])
                if r["status"] != "usable":
                    rejected.append(it["id"])
                    reasons.append(f"{it['id']}: {r['why']}")
                    continue
                usable.append(it["id"])
                p = plan.get(ctx, tid)
                for st in p["plan"]["steps"]:
                    steps_total += 1
                    steps_three += [w["way"] for w in st["explain"]] == list(plan.WAYS)
                    for c in [w["cite"] for w in st["explain"]] + ([st["check"]["cite"]] if st["check"] else []):
                        cites_total += 1
                        cites_real += sources.find_quote(ctx, c["source_id"], c["chunk_no"], c["quote"])
                for k in ("guided", "solo"):
                    c = p["plan"][k]["cite"]
                    cites_total += 1
                    cites_real += sources.find_quote(ctx, c["source_id"], c["chunk_no"], c["quote"])
                if shown < a.dump:
                    shown += 1
                    print(show(it, p) + "\n")
    finally:
        ask.think = orig
        _harness.cleanup(home)
    done = len(usable) + len(rejected)
    avg = sum(costs) / len(costs) if costs else 0.0
    print(f"usable {len(usable)} of {done} run ({len(items)} in the set)   [bar: 8 of 10]")
    print(f"cites real {cites_real} of {cites_total}   [bar: all]    three ways {steps_three} of {steps_total} steps   [bar: all]")
    print(f"cost per plan avg ${avg:.4f}, max ${max(costs, default=0):.4f}   [bar: under $0.05]    total ${spent['usd']:.4f} (model {a.model})")
    for r in reasons:
        print("  rejected:", r)
    if pending:
        print(f"paid, pending: {len(pending)} topics not run (budget): {', '.join(pending)}")
    bad = cites_real < cites_total or steps_three < steps_total or (max(costs, default=0) >= 0.05)
    if done == len(items) and len(usable) < 8:
        bad = True
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.10)
    ap.add_argument("--only", type=int, default=0)
    ap.add_argument("--model", default="haiku", choices=["haiku", "strong"])
    ap.add_argument("--dump", type=int, default=0)
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
