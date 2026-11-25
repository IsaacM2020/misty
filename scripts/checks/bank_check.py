#!/usr/bin/env python3
"""E10 scored check: the question bank. PAID (Haiku with thinking) unless --dry, stops at --max-usd.

    engine/.venv/bin/python scripts/checks/bank_check.py --set tests/engine/fixtures/plan_set.json --max-usd 0.06 [--only N] [--model haiku|strong] [--dump 3]
    engine/.venv/bin/python scripts/checks/bank_check.py --dry          # free: a fake model, proves the plumbing only

Same 10 topics as plan_check.py. Bars: every cite a real quote (100%, code, rechecked here), every kept key passes E6 again, at least
9 usable (3 per level) on 8 of 10 topics, cost per question under $0.004, no question text in a topic twice.
`--dump N` prints N topics' questions in plain text for Isaac to skim (good or silly is his call, not pass or fail).
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

DEFAULT_SET = _harness.ROOT / "tests" / "engine" / "fixtures" / "plan_set.json"
PER_Q_BAR = 0.004


def first_quote(text: str) -> str:
    return " ".join(text.split(". ")[0].strip().split()[:18])


def fake_think(quote_by_src):
    """--dry: nine questions with made-up words and a term key from the quote, so the code path is proved without a model."""
    async def think(ctx, system, user, **kw):
        import re
        sid = re.search(r"source_id: (\S+)", user).group(1)
        chunk = int(re.search(r"chunk (\d+)", user).group(1))
        quote = quote_by_src[sid]
        words = [w for w in quote.split() if len(w) > 4] or quote.split()
        out, n = [], 0
        for m in re.finditer(r"level (\d): (\d+)", user):
            for _ in range(int(m.group(2))):
                n += 1
                out.append({"level": int(m.group(1)), "q": f"Item {' '.join(f'z{n}x{j}' for j in range(6))}: which word fits?",
                            "key": {"accept": [words[n % len(words)]]}, "cite": {"source_id": sid, "chunk_no": chunk, "quote": quote}})
        return json.dumps({"questions": out}), 0.0
    return think


async def main_async(a) -> int:
    from misty_engine.openrouter import user_approved
    from misty_engine.teach import ask, bank, plan, sources
    from misty_engine.teach import map as tm
    ctx, home = _harness.temp_ctx(copy_live=False)
    items = json.loads(Path(a.set).read_text())
    if a.only:
        items = items[:a.only]
    spent = {"usd": 0.0}
    orig = ask.think
    quotes: dict[str, str] = {}
    if a.dry:
        ask.think = fake_think(quotes)
    else:
        async def metered(ctx_, *args, **kw):
            if spent["usd"] >= a.max_usd:
                from misty_engine.errors import MistyError
                raise MistyError("budget reached")
            text, usd = await orig(ctx_, *args, **kw)
            spent["usd"] += usd
            return text, usd
        ask.think = metered
    ok_topics, pending, per_q, reasons = [], [], [], []
    cites_total = cites_real = keys_total = keys_ok = dup_texts = made_total = 0
    shown = 0
    try:
        with user_approved():
            for it in items:
                if not a.dry and spent["usd"] >= a.max_usd:
                    pending.append(it["id"])
                    continue
                src = sources._keep(ctx, "page", f"https://fixture.test/{it['id']}", it["id"], "\n\n".join(it["paras"]))
                cs = sources.chunks(ctx, src["id"])
                q = first_quote(it["paras"][0])
                no = next(n for n, t in cs if sources.find_quote(ctx, src["id"], n, q))
                quotes[src["id"]] = q
                sid = tm.add_subject(ctx, it["subject"], it["subject_title"])["id"]
                tid = tm.add_topic(ctx, sid, it["id"], it["topic"], {"source_id": src["id"], "chunk_no": no, "quote": q})["id"]
                r = await bank.make(ctx, tid, model_key=a.model)
                rows = ctx.con.execute("SELECT * FROM teach_questions WHERE topic_id=? AND source='bank' AND status='usable'", (tid,)).fetchall()
                made_total += len(rows)
                if rows:
                    per_q.append(r["cost"] / len(rows))
                if len(rows) >= 9 and min(bank.counts(ctx, tid).values()) >= 3:
                    ok_topics.append(it["id"])
                else:
                    reasons.append(f"{it['id']}: {len(rows)} usable {bank.counts(ctx, tid)}")
                texts = [" ".join(x["q"].lower().split()) for x in rows]
                dup_texts += len(texts) - len(set(texts))
                for x in rows:
                    c, k = json.loads(x["cite_json"]), json.loads(x["key_json"])
                    cites_total += 1
                    cites_real += bool(sources.find_quote(ctx, c["source_id"], c["chunk_no"], c["quote"]))
                    keys_total += 1
                    keys_ok += bool((await plan.check_key(ctx, x["q"], k, c))["ok"])
                if shown < a.dump:
                    shown += 1
                    print(f"=== {it['subject_title']}: {it['topic']}")
                    for x in rows:
                        print(f"  L{x['level']} {x['q']}   key={x['key_json']}")
    finally:
        ask.think = orig
        _harness.cleanup(home)
    done = len(items) - len(pending)
    avg = sum(per_q) / len(per_q) if per_q else 0.0
    print(f"{'DRY (fake model)' if a.dry else 'PAID'}: {len(ok_topics)} of {done} topics with 9+ usable (3 per level)   [bar: 8 of 10]")
    print(f"cites real {cites_real} of {cites_total}   [bar: all]    keys pass E6 again {keys_ok} of {keys_total}   [bar: all]    repeated texts {dup_texts}   [bar: 0]")
    print(f"cost per question avg ${avg:.4f}, max ${max(per_q, default=0):.4f}   [bar: under ${PER_Q_BAR}]    total ${spent['usd']:.4f} (model {a.model})    questions {made_total}")
    for r in reasons:
        print("  short:", r)
    if pending:
        print(f"paid, pending: {len(pending)} topics not run (budget): {', '.join(pending)}")
    bad = cites_real < cites_total or keys_ok < keys_total or dup_texts or max(per_q, default=0) >= PER_Q_BAR
    if done == len(items) and len(ok_topics) < min(8, len(items)):
        bad = True
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(DEFAULT_SET))
    ap.add_argument("--max-usd", type=float, default=0.06)
    ap.add_argument("--only", type=int, default=0)
    ap.add_argument("--model", default="haiku", choices=["haiku", "strong"])
    ap.add_argument("--dump", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
