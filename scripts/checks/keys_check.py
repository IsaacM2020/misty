#!/usr/bin/env python3
"""E6 scored check: the answer checker. PAID (Haiku with thinking), stops at --max-usd.

    engine/.venv/bin/python scripts/checks/keys_check.py [--max-usd 0.07] [--free] [--kinds number,choice,term,points]

Fixture: tests/engine/fixtures/keys_set.json, 60 right keys (20 number, 20 choice, 10 term + 10 worded points) whose answers a
textbook or mark scheme confirms, plus 40 planted wrong keys made in code from 40 of them:
  number x10, sign flipped, wrong unit | choice the next option | term a form that is not in the passage |
  points a made-up quote, or a true quote under a claim the passage contradicts.
Bars:  planted wrong keys through: 0 of 40.  right keys passing: at least 45 of 60.  grading: at least 28 of 30.  cost per key under $0.004.
A second solve never sees the key, so a right key and its planted copy share one solve; it is paid once.
`--free` runs only what needs no model (terms, made-up quotes, grading). If the money stops first, the rest is printed as "paid, pending".
Runs on an empty throwaway data folder with its own spend meter. Exit 1 if a planted key got through, or a finished bar is missed.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

FIX = _harness.ROOT / "tests" / "engine" / "fixtures" / "keys_set.json"
WRONG_UNIT = {"m/s": "m/s^2", "m": "m/s", "N": "J", "W": "J", "J": "W", "A": "V", "g/cm3": "kg/m3", "g/mol": "g", "dm3": "cm3", "%": "g"}


def planted(kind: str, item: dict):
    """The wrong key made from a right one, and how it was broken. None when the item is not planted."""
    how = item.get("planted")
    if not how:
        return None
    key = copy.deepcopy(item["key"])
    if kind == "number":
        if how == "x10":
            key["expr"] = f"({key['expr']})*10"
        elif how == "sign":
            key["expr"] = f"-({key['expr']})"
        else:
            key["unit"] = WRONG_UNIT[key["unit"]]
    elif kind == "choice":
        o = key["options"]
        key["answer"] = o[(o.index(key["answer"]) + 1) % len(o)]
    elif kind == "term":
        key["accept"] = [how]
    else:
        bad = item["bad_point"]
        if how == "fake":
            key["points"][-1] = {"text": bad["text"], "quote": bad["text"]}
        else:
            key["points"][-1] = {"text": bad["text"], "quote": bad["quote"]}
    return key, how


def cases(kinds):
    fx = json.loads(FIX.read_text())
    per = {k: fx[k] for k in kinds}
    order = []
    for i in range(max(len(v) for v in per.values())):                # round robin: a short run still covers every kind
        for k in kinds:
            if i < len(per[k]):
                order.append((k, per[k][i]))
    return order


def grading_cases(fx):
    out = []
    for it in [x for x in fx["number"] if x["key"]["unit"]][:6]:
        from misty_engine.teach import keys
        v = keys._calc(it["key"]["expr"])
        u = it["key"]["unit"]
        out += [(it["key"], f"{v:g} {u}", "right"), (it["key"], f"{v:g}", "partly"), (it["key"], f"{v * 1.03:g} {u}", "partly"), (it["key"], f"{v * 3:g} {u}", "wrong")]
    for it in fx["choice"][:3]:
        o, a = it["key"]["options"], it["key"]["answer"]
        i = o.index(a)
        out += [(it["key"], "ABCD"[i], "right"), (it["key"], "ABCD"[(i + 1) % 4], "wrong")]
    return out


async def main_async(a) -> int:
    from misty_engine.openrouter import user_approved
    from misty_engine.teach import keys, sources
    ctx, home = _harness.temp_ctx(copy_live=False)
    kinds = a.kinds.split(",")
    spent = {"usd": 0.0, "calls": 0}
    cache: dict = {}

    def memo(name):                                                   # one solve per question, shared by the right key and its planted copy
        orig = getattr(keys, name)

        async def wrapped(ctx_, *args):
            k = repr((name, *args))
            if k in cache:
                return cache[k][0], 0.0
            if a.free:
                raise RuntimeError("paid call skipped (--free)")
            if spent["usd"] >= a.max_usd:
                raise RuntimeError("budget reached")
            res = await orig(ctx_, *args)
            cache[k] = res
            spent["usd"] += res[1]
            spent["calls"] += 1
            return res
        setattr(keys, name, wrapped)
    for n in ("second_solve_number", "second_solve_choice", "reject"):
        memo(n)

    right = {k: [0, 0, 0] for k in kinds}                             # passed, failed, pending
    wrong = {k: [0, 0, 0] for k in kinds}                             # through, stopped, pending
    through, failed = [], []
    keycost: list[float] = []
    try:
        with user_approved():
            for kind, it in cases(kinds):
                src = sources._keep(ctx, "page", f"https://fixture.test/{it['id']}", it["id"], it["passage"])
                cite = {"source_id": src["id"], "chunk_no": 0}
                for label, key, bucket in (("right", it["key"], right), ("planted", None, wrong)):
                    if label == "planted":
                        p = planted(kind, it)
                        if p is None:
                            continue
                        key = p[0]
                    before = spent["usd"]
                    r = await keys.check(ctx, it["q"], key, cite)
                    if "budget reached" in r["why"] or "paid call skipped" in r["why"] or "could not finish" in r["why"]:
                        bucket[kind][2] += 1
                        continue
                    if spent["usd"] > before:
                        keycost.append(spent["usd"] - before)
                    if label == "right":
                        bucket[kind][0 if r["ok"] else 1] += 1
                        if not r["ok"]:
                            failed.append(f"{it['id']}: {r['why']}")
                    else:
                        bucket[kind][0 if r["ok"] else 1] += 1
                        if r["ok"]:
                            through.append(f"{it['id']} ({p[1]})")
    finally:
        _harness.cleanup(home)
    fx = json.loads(FIX.read_text())
    g = grading_cases(fx)
    ctx2, home2 = _harness.temp_ctx(copy_live=False)
    try:
        got = [(await keys.grade(ctx2, "q", k, ans))["verdict"] for k, ans, _ in g]
    finally:
        _harness.cleanup(home2)
    gok = sum(1 for (k, ans, want), v in zip(g, got) if v == want)

    print(f"{'kind':<8} {'right pass':>11} {'right fail':>11} {'pending':>8} | {'planted THROUGH':>16} {'stopped':>8} {'pending':>8}")
    for k in kinds:
        print(f"{k:<8} {right[k][0]:>11} {right[k][1]:>11} {right[k][2]:>8} | {wrong[k][0]:>16} {wrong[k][1]:>8} {wrong[k][2]:>8}")
    rp = sum(v[0] for v in right.values()); rf = sum(v[1] for v in right.values()); rn = sum(v[2] for v in right.values())
    wt = sum(v[0] for v in wrong.values()); ws = sum(v[1] for v in wrong.values()); wn = sum(v[2] for v in wrong.values())
    print(f"\nplanted wrong keys through: {wt} of {wt + ws + wn}   (checked {wt + ws}, pending {wn})   bar: 0 of 40")
    print(f"right keys passing: {rp} of {rp + rf + rn}   (checked {rp + rf}, pending {rn})   bar: 45 of 60")
    print(f"grading: {gok} of {len(g)} as expected   bar: 28 of 30")
    per = sum(keycost) / len(keycost) if keycost else 0.0
    print(f"paid: ${spent['usd']:.4f} in {spent['calls']} calls (limit ${a.max_usd:.2f}); about ${per:.4f} per paid key   bar: under $0.004")
    for t in through:
        print("  GOT THROUGH:", t)
    for f in failed:
        print("  right key rejected:", f)
    pending = rn + wn
    if pending:
        print(f"\nPAID, PENDING: {pending} checks not run. Command: engine/.venv/bin/python scripts/checks/keys_check.py --max-usd 0.30")
    bad = wt > 0 or gok < 28 or (not pending and rp < 45)
    print("\nCHECK:", "FAIL" if bad else ("PASS (partial, see pending)" if pending else "PASS"))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.07)
    ap.add_argument("--free", action="store_true")
    ap.add_argument("--kinds", default="number,choice,term,points")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
