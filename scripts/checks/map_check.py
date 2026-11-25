#!/usr/bin/env python3
"""E3 scored check: build the map from one real source and print what came out. PAID (a few cents, Haiku with thinking).

    engine/.venv/bin/python scripts/checks/map_check.py FILE [--subject NAME] [--model haiku|strong] [--max-usd 0.10]

Runs on an empty throwaway data folder (no personal data, its own spend meter). It prints:
  quotes found    every topic's quote looked up again in its chunk. Code made that list, so anything under 100% is a bug.
  cost            under --max-usd or the check fails.
  the order       every topic with what it needs, for Isaac to skim: mark each wrong "this needs that". Bar: at most 1 in 10.
Exit 1 when a quote is missing, the map did not finish or the cost is over.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402


async def main_async(a) -> int:
    from misty_engine.openrouter import user_approved
    from misty_engine.teach import build as tb
    from misty_engine.teach import map as tm
    from misty_engine.teach import sources as ts
    ctx, home = _harness.temp_ctx(copy_live=False)
    try:
        src = await ts.add(ctx, str(a.file))
        subj = tm.add_subject(ctx, a.subject, a.subject)["id"]
        with user_approved():
            r = await tb.build_map(ctx, src["id"], subj, model_key=a.model)
        rows = tb.ordered(ctx, subj)
        found = sum(ts.find_quote(ctx, src["id"], t["chunk_no"], t["quote"]) for t in rows)
        needs = sum(len(t["needs"]) for t in rows)
        print(f"source: {src['title']} ({src['chars']:,} chars)  model: {a.model}")
        print(f"status: {r['status']}  topics: {r['topics']}  dropped (bad quote): {r['dropped']}  loops refused: {r['loops']}  merged: {r['merged']}  needs: {needs}")
        print(f"quotes found: {found} of {len(rows)} ({100 * found / max(1, len(rows)):.0f}%)")
        print(f"cost: ${r['usd']:.4f} (limit ${a.max_usd:.2f})\n")
        for i, t in enumerate(rows, 1):
            print(f"{i:>2}. {t['title']}" + (f"   <- needs: {', '.join(t['needs_titles'])}" if t["needs"] else ""))
        ok = r["status"] == "done" and found == len(rows) and rows and r["usd"] <= a.max_usd
        print("\nCHECK:", "PASS (Isaac still skims the order: at most 1 in 10 'needs' links wrong)" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        _harness.cleanup(home)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--subject", default="check subject")
    ap.add_argument("--model", default="haiku", choices=["haiku", "strong"])
    ap.add_argument("--max-usd", type=float, default=0.10)
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
