#!/usr/bin/env python3
"""Local (Mac Mini, Inkling 2 Mini) against cloud Haiku on real background inputs. Paid, but capped at $0.05 of cloud.

    engine/.venv/bin/python scripts/checks/local_vs_cloud.py [--n 8] [--chat 4] [--batch 2] [--cap 0.05] [--only summarise,compile,chat_compact]

Inputs are rebuilt from a copy of the live db the way the engine builds them (summarise: wiki/summaries.summary_inputs;
compile: cloud.compile_batch's render_batch/build_messages; chat_compact: chat.compact's prompt over real chat history).
Each goes once to the cloud (Haiku, through the engine's own client) and once to the local server (through local_llm's
own request, so the no-thinking prefill is what is measured), and the report shows both outputs side by side with
latency, tokens, and whether the local reply passed the parser the engine would apply to it.

Writes ~/Misty/checks/local_vs_cloud-<date>.md. The live db is only read; nothing is written to it.
Entities are asked in small batches (--batch) because the Mini's context is 16k; the report also says how big the engine's
real batches (8 entities) are, since a batch that does not fit goes to the cloud on its own.
"""
import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import cleanup, temp_ctx  # noqa: E402  (also puts engine/src on sys.path)

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=8, help="inputs each for summarise and compile")
ap.add_argument("--chat", type=int, default=4, help="inputs for chat_compact")
ap.add_argument("--batch", type=int, default=2, help="entities / items per input")
ap.add_argument("--cap", type=float, default=0.05, help="most cloud spend, USD")
ap.add_argument("--only", default="summarise,compile,chat_compact")
ap.add_argument("--out", default=None)
args = ap.parse_args()


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


async def main() -> None:
    from misty_engine import cloud, persona
    from misty_engine import chat as chatmod
    from misty_engine.clock import sgt_date
    from misty_engine.compile.haiku import build_messages, parse_response
    from misty_engine.compile.prompts import BatchItem, render_batch
    from misty_engine.errors import MistyError
    from misty_engine.local_llm import json_object
    from misty_engine.openrouter import user_approved
    from misty_engine.registry.hints import known_block
    from misty_engine.registry.store import Registry
    from misty_engine.scrub import scrub_text
    from misty_engine.types import approx_tokens
    from misty_engine.wiki import summaries as S

    ctx, home = temp_ctx(copy_live=True)
    local = ctx.openrouter.local
    cfg = {**local.config(), "enabled": True}
    ctx.settings.set("local_llm", {**cfg, "enabled": False})       # off: ctx.openrouter.chat() must reach the real cloud. The local side calls the server directly
    timeout = float(cfg["timeout_s"])
    only = set(args.only.split(","))
    spent = 0.0
    rows: list[dict] = []                      # one per (purpose, input)

    async def run_local(messages, max_tokens, json_out):
        """The same request local_llm sends, but the raw text comes back even when the engine would reject it."""
        t0 = time.perf_counter()
        try:
            data = await local._post(cfg, messages, max_tokens, 0.1, json_out, timeout)
            res = local._read(data, json_out, 0)
            return {"text": res.text, "finish": res.finish_reason, "usage": res.usage, "err": None,
                    "ms": int((time.perf_counter() - t0) * 1000)}
        except Exception as e:                                   # LocalFailure carries .why
            return {"text": "", "finish": "", "usage": {}, "err": getattr(e, "why", type(e).__name__),
                    "ms": int((time.perf_counter() - t0) * 1000)}

    async def run_cloud(messages, max_tokens, purpose):
        nonlocal spent
        if spent >= args.cap:
            return {"text": "", "finish": "", "usage": {}, "err": "cloud cap reached, skipped", "ms": None, "usd": 0.0}
        t0 = time.perf_counter()
        try:
            with user_approved():
                r = await ctx.openrouter.chat(model_key="haiku", messages=messages, max_tokens=max_tokens, job_id=None,
                                              phase="live", purpose=purpose, temperature=0.1)
        except MistyError as e:
            return {"text": "", "finish": "", "usage": {}, "err": f"{type(e).__name__}:{e}", "ms": None, "usd": 0.0}
        spent += r.cost_usd
        if r.cost_usd == 0.0:
            raise SystemExit("a cloud call cost $0: it did not reach the cloud (is local_llm enabled in settings?)")
        return {"text": r.text, "finish": r.finish_reason, "usage": r.usage, "err": None, "usd": r.cost_usd,
                "ms": int((time.perf_counter() - t0) * 1000)}

    # ------------------------------------------------------------ summarise
    if "summarise" in only:
        reg = Registry(ctx.con, ctx.clock)
        ranked = sorted(S.rank_scores(ctx).items(), key=lambda kv: -kv[1])
        stale = S.stale_entities(ctx)
        pool = [e for e in stale if e in dict(ranked)] or [e for e, _ in ranked]
        pool = pool[: args.n * args.batch * 3]                        # most important first, then thin it out
        pool = pool[:: max(1, len(pool) // (args.n * args.batch))] if len(pool) > args.n * args.batch else pool
        groups = [pool[i:i + args.batch] for i in range(0, len(pool), args.batch)][: args.n]
        # how big are the engine's real batches?
        big = S.stale_entities(ctx)[: S.BATCH * 3] or [e for e, _ in ranked][: S.BATCH * 3]
        real = [sum(approx_tokens(p) for e in big[i:i + S.BATCH] for p in S.summary_inputs(ctx, e)) + approx_tokens(S.SYSTEM)
                for i in range(0, len(big), S.BATCH)]
        for gi, eids in enumerate(groups):
            parts = [scrub_text(p, "file").text for e in eids for p in S.summary_inputs(ctx, e)]
            msgs = [{"role": "system", "content": S.SYSTEM.strip()}, {"role": "user", "content": "\n\n".join(parts)}]
            mt = 350 * len(eids) + 300
            names = ", ".join(f"{reg.get(e)['name']} ({reg.get(e)['kind']})" for e in eids)

            def check(text, eids=eids):
                d = json_object(text)
                if d is None:
                    return False, "not a JSON object"
                by = {x.get("id"): x for x in (d.get("entities") or []) if isinstance(x, dict)}
                bad = [e for e in eids if e not in by or S.validate_summary(by[e], reg.get(e)["kind"]) is None]
                return (not bad), (f"{len(eids) - len(bad)}/{len(eids)} entities valid" if bad else "all entities valid")
            print(f"summarise {gi + 1}/{len(groups)}: {names}", flush=True)
            c = await run_cloud(msgs, mt, "summarise")
            l = await run_local(msgs, mt, True)
            rows.append({"purpose": "summarise", "label": names, "in_tokens": approx_tokens(json.dumps(msgs)),
                         "cloud": c, "local": l, "check": check})
        rows_summ_note = (f"The engine batches {S.BATCH} entities per call: the {len(real)} real batches checked are about "
                          f"{', '.join(map(str, real))} input tokens; the Mini's context is {cfg['ctx']}, so a batch over "
                          f"~{cfg['ctx'] - 400 - 350 * S.BATCH - 300} tokens goes to the cloud on its own.")
    else:
        rows_summ_note = ""

    # ------------------------------------------------------------ compile
    if "compile" in only:
        cand = ctx.con.execute(
            "SELECT i.* FROM items i WHERE i.kind IN ('file','repo','mail_thread','calendar_week','chat_week') "
            "AND i.status='done' AND i.matters=1 AND EXISTS (SELECT 1 FROM item_chunks c WHERE c.item_id=i.id) "
            "ORDER BY i.updated_at DESC LIMIT 400").fetchall()
        sized = [(r, cloud.item_tokens(ctx, r["id"])) for r in cand]
        sized = [(r, t) for r, t in sized if 150 <= t <= 1100]
        step = max(1, len(sized) // (args.n * args.batch))
        pick = [r for r, _ in sized[::step]][: args.n * args.batch]
        groups = [pick[i:i + args.batch] for i in range(0, len(pick), args.batch)]
        for gi, rs in enumerate(groups):
            texts = {r["id"]: cloud.item_text(ctx, r["id"]) for r in rs}
            batch = [BatchItem(r["id"], r["source"], r["kind"], r["title"], texts[r["id"]], *cloud._chat_labels(r)) for r in rs]
            known = known_block(ctx, rs, texts=texts)
            msgs = build_messages(render_batch(batch, sgt_date(ctx.clock.now()), known))
            msgs = [{"role": m["role"], "content": "".join(p["text"] for p in m["content"]) if isinstance(m["content"], list)
                     else m["content"]} for m in msgs]
            mt = min(8000, 800 * len(batch) + 600)
            live = [r["id"] for r in rs]

            def check(text, live=live):
                p = parse_response(text, live)
                return (not p.bad), f"{len(p.ok)} ok, {len(p.bad)} bad" + (f" ({', '.join(sorted(set(p.bad.values())))})" if p.bad else "")
            label = "; ".join(f"{r['kind']}: {(r['title'] or '')[:50]}" for r in rs)
            print(f"compile {gi + 1}/{len(groups)}: {label}", flush=True)
            # the cloud call in the engine sends the cached system block; the content is the same
            c = await run_cloud(build_messages(render_batch(batch, sgt_date(ctx.clock.now()), known)), mt, "compile")
            l = await run_local(msgs, mt, True)
            rows.append({"purpose": "compile", "label": label, "in_tokens": approx_tokens(json.dumps(msgs)),
                         "cloud": c, "local": l, "check": check})

    # ------------------------------------------------------------ chat_compact
    if "chat_compact" in only and args.chat > 0:
        live_db = sqlite3.connect(f"file:{Path.home() / 'Misty' / 'misty.db'}?mode=ro", uri=True)
        msgs_all = live_db.execute("SELECT role, text FROM chat_messages WHERE role IN ('user','assistant') ORDER BY id").fetchall()
        live_db.close()
        win = 14
        starts = list(range(0, max(0, len(msgs_all) - win), max(1, (len(msgs_all) - win) // max(1, args.chat))))[: args.chat]
        for gi, s in enumerate(starts):
            old = msgs_all[s:s + win]
            convo = "\n".join(f"{'<<name>>' if role == 'user' else 'Misty'}: {text[:1500]}" for role, text in old)
            msgs = [{"role": "system", "content": persona.render(ctx, chatmod.COMPACT_PROMPT)},
                    {"role": "user", "content": chatmod._clean(persona.render(ctx, f"Summary so far:\n(none)\n\nNew messages:\n{convo}"))}]

            def check(text):
                t = chatmod._clean(text).strip()
                words = len(t.split())
                return bool(t) and words <= 260, f"{words} words" + ("" if words <= 200 else " (over the 200 asked for)")
            print(f"chat_compact {gi + 1}/{len(starts)}: {len(old)} messages", flush=True)
            c = await run_cloud(msgs, 400, "chat_compact")
            l = await run_local(msgs, 400, False)
            rows.append({"purpose": "chat_compact", "label": f"{len(old)} real messages from position {s}",
                         "in_tokens": approx_tokens(json.dumps(msgs)), "cloud": c, "local": l, "check": check})

    # ------------------------------------------------------------ report
    out = Path(args.out) if args.out else Path.home() / "Misty" / "checks" / f"local_vs_cloud-{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    L: list[str] = []
    L.append(f"# Local vs cloud: {date.today().isoformat()}\n")
    L.append(f"Local: `{cfg['model']}` at {cfg['url']} (no-thinking prefill, ctx {cfg['ctx']}). Cloud: Haiku through the engine's OpenRouter client. "
             f"Cloud spend this run: **${spent:.4f}** (cap ${args.cap:.2f}). Inputs rebuilt from a copy of the live db.\n")
    L.append("| purpose | inputs | local passed the parser | median ms local | median ms cloud | median out tokens local | median out tokens cloud | local fell over (would go to cloud) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for p in ("summarise", "compile", "chat_compact"):
        rs = [r for r in rows if r["purpose"] == p]
        if not rs:
            continue
        for r in rs:
            r["ok"], r["detail"] = (False, f"local error: {r['local']['err']}") if r["local"]["err"] else r["check"](r["local"]["text"])
            if r["local"]["finish"] == "length":
                r["ok"], r["detail"] = False, "cut off at max_tokens"
            r["cok"], r["cdetail"] = (False, r["cloud"]["err"]) if r["cloud"]["err"] else r["check"](r["cloud"]["text"])
        okn = sum(r["ok"] for r in rs)
        L.append(f"| {p} | {len(rs)} | {okn}/{len(rs)} | {med([r['local']['ms'] for r in rs if not r['local']['err']])} | "
                 f"{med([r['cloud']['ms'] for r in rs])} | "
                 f"{med([r['local']['usage'].get('completion_tokens') for r in rs])} | "
                 f"{med([r['cloud']['usage'].get('completion_tokens') for r in rs])} | {len(rs) - okn} |")
    if rows_summ_note:
        L.append(f"\n{rows_summ_note}\n")
    for p in ("summarise", "compile", "chat_compact"):
        rs = [r for r in rows if r["purpose"] == p]
        if not rs:
            continue
        L.append(f"\n## {p}\n")
        for i, r in enumerate(rs, 1):
            lo, cl = r["local"], r["cloud"]
            L.append(f"### {p} {i}: {r['label']}\n")
            L.append(f"input ~{r['in_tokens']} tokens\n")
            L.append(f"- local: {lo['ms']} ms, in {lo['usage'].get('prompt_tokens', '?')} / out {lo['usage'].get('completion_tokens', '?')} tokens, "
                     f"finish `{lo['finish'] or '-'}`, **parser: {'PASS' if r['ok'] else 'FAIL'}** ({r['detail']})")
            L.append(f"- cloud: {cl['ms']} ms, in {cl['usage'].get('prompt_tokens', '?')} / out {cl['usage'].get('completion_tokens', '?')} tokens, "
                     f"${cl.get('usd', 0):.5f}, parser: {'PASS' if r['cok'] else 'FAIL'} ({r['cdetail']})\n")
            for who, o in (("Cloud (Haiku)", cl), ("Local (Inkling 2 Mini)", lo)):
                body = o["text"] or f"(no output: {o['err']})"
                L.append(f"**{who}**\n\n```\n{body[:2500]}{'...' if len(body) > 2500 else ''}\n```\n")
    out.write_text("\n".join(L))
    print(f"\nreport: {out}\ncloud spend ${spent:.4f}")
    cleanup(home)


asyncio.run(main())
