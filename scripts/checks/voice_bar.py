#!/usr/bin/env python3
"""Voice bar: do spoken replies sound like a person? PAID (about $0.003 a turn, 20 turns, under $0.25).

    engine/.venv/bin/python scripts/checks/voice_bar.py [--max-usd 0.25]

Three conversations on throwaway copies of the live db (_harness.temp_ctx), real model, voice=True:
  A fresh normal, B stale gap (old econ thread, last messages 2 days ago), C long-answer asks.
Checks per reply: <=2 sentences unless the Slate opened, no markdown/list markers, no "above/below/as shown/this table/here are",
questions (target <=1 in 5), the address word (<=1 in 3 replies, never the last word), B's first reply must not name the old topic.
Then first-token latency (median, p90) and prompt_built median. Never changes engine code or the live ~/Misty.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

A = ["what's up", "explain price elasticity of demand", "what's 17 times 23", "why", "how do I balance a redox equation",
     "ok thanks", "what's the time", "give me a quick motivational line", "what's the capital of Peru",
     "remind me what we were talking about"]
B = ["what's up", "anything I should do today", "cool"]
C = ["list the steps to solve a quadratic", "what are the three types of unemployment", "compare mitosis and meiosis",
     "tell me more", "summarise the causes of world war one", "give me five revision tips", "explain how a transistor works"]
OLD_THREAD = [("user", "can you help me with my econ homework, the one on elasticity of supply"),
              ("assistant", "Sure. Price elasticity of supply is the percentage change in quantity supplied over the percentage change in price."),
              ("user", "ok and what if the supply is perfectly inelastic, like concert tickets"),
              ("assistant", "Then the supply curve is vertical, so the quantity doesn't move whatever the price does. Your homework question 3 is that case.")]
OLD_WORDS = ["elasticity", "supply", "homework", "econ", "concert", "ticket"]
BAD_REF = re.compile(r"\b(above|below|as shown|this table|here are)\b", re.I)
MARKDOWN = re.compile(r"^\s*(?:[-*•]\s|\d+[.)]\s|#|\*\*)", re.M)
SENT = re.compile(r"[.!?]+(?=\s|$)")


def sentences(t: str) -> int:
    return len(SENT.findall(t.strip()))


def make_old_thread(ctx) -> None:
    """Old econ thread, then every message moved to 2 days ago."""
    ago = time.time() - 2 * 86400
    for i, (role, text) in enumerate(OLD_THREAD):
        ctx.con.execute("INSERT INTO chat_messages(role, text, meta, created_at) VALUES (?,?,?,?)", (role, text, "{}", ago - 600 + i * 30))
    ctx.con.commit()


async def run_convo(name: str, lines: list[str], stale: bool, rows: list[dict], state: dict) -> None:
    from misty_engine import chat, ways
    from misty_engine.openrouter import user_approved
    from misty_engine.turn_timing import Marks
    ctx, home = _harness.temp_ctx(copy_live=True)
    from misty_engine.errors import Fatal
    from misty_engine.sources import google_auth

    def no_google(*_a, **_k):                       # the keychain read for Google's token blocks on a GUI prompt when run headless;
        raise Fatal("NEEDS_AUTH")                   # a calendar/mail tool call then answers "Google isn't connected" and the turn goes on
    google_auth.service = no_google
    try:
        if stale:
            make_old_thread(ctx)
        state["address"] = ways.address(ctx.con) or ""

        async def nothing(_):
            return None

        async def no(_):
            state["denied"] = state.get("denied", 0) + 1
            return False
        for i, text in enumerate(lines):
            if state["spent"] >= state["max"]:
                print(f"money cap hit, stopping before {name}{i + 1}")
                return
            marks, parts = Marks(), []

            async def on_delta(p):
                parts.append(p)
            err = ""
            out: dict = {}
            try:
                with user_approved():
                    out = await chat.send(ctx, text, on_delta=on_delta, on_status=nothing, confirm=no, voice=True, marks=marks)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            it = dict(marks.items)
            usd = float(out.get("usd") or 0.0)
            state["spent"] += usd
            tools = [a.get("tool") for a in out.get("actions", [])]
            rows.append({"id": f"{name}{i + 1}", "q": text, "reply": (out.get("reply") or "".join(parts)).strip(), "tools": tools,
                         "first": it.get("first_delta_out"), "built": it.get("prompt_built"), "http": it.get("http_start"),
                         "usd": usd, "err": err})
            print(f"  {name}{i + 1} ${usd:.4f} first={it.get('first_delta_out')}ms tools={tools} {err}", flush=True)
    finally:
        _harness.cleanup(home)


def judge(rows: list[dict], word: str) -> list[dict]:
    for r in rows:
        t, f = r["reply"], []
        n = sentences(t)
        r["n"] = n
        if n > 2 and "slate" not in r["tools"]:
            f.append(f"sentences={n}")
        if MARKDOWN.search(t):
            f.append("markdown")
        if BAD_REF.search(t) and "slate" not in r["tools"]:
            f.append("pointing-word")
        r["q_end"] = t.rstrip().endswith("?")
        w = word.lower()
        words = re.findall(r"[\w']+", t.lower())
        r["has_word"] = bool(w) and w in words
        r["last_word"] = bool(w) and bool(words) and words[-1] == w
        if r["last_word"]:
            f.append("address-last")
        if r["id"] == "B1" and any(o in t.lower() for o in OLD_WORDS):
            f.append("old-topic")
        if not t:
            f.append("empty/" + r["err"])
        r["fails"] = f
    return rows


def pct(v: list, p: float):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.25)
    a = ap.parse_args()
    rows: list[dict] = []
    state = {"spent": 0.0, "max": a.max_usd}

    async def go():
        await run_convo("A", A, False, rows, state)
        await run_convo("B", B, True, rows, state)
        await run_convo("C", C, False, rows, state)
    asyncio.run(go())
    word = state.get("address", "")
    judge(rows, word)
    n = len(rows)
    print(f"\naddress word: {word!r}\n")
    print(f"{'id':<4}{'sent':>5} {'?':>2} {'adr':>3} {'last':>4} {'first_ms':>8} {'built':>6}  tools / fails")
    for r in rows:
        print(f"{r['id']:<4}{r['n']:>5} {'?' if r['q_end'] else '-':>2} {'y' if r['has_word'] else '-':>3} {'LAST' if r['last_word'] else '-':>4} "
              f"{str(r['first']):>8} {str(r['built']):>6}  {','.join(r['tools']) or '-'} {' | '.join(r['fails'])}")
    q, w, lw = sum(r["q_end"] for r in rows), sum(r["has_word"] for r in rows), sum(r["last_word"] for r in rows)
    fails = {k: sum(1 for r in rows if any(f.startswith(k) for f in r["fails"])) for k in ("sentences", "markdown", "pointing", "old-topic")}
    firsts = [r["first"] for r in rows]
    print(f"\nreplies {n}; >2 sentences (no slate): {fails['sentences']}; markdown: {fails['markdown']}; pointing words: {fails['pointing']}; old topic in B1: {fails['old-topic']}")
    print(f"ending in '?': {q} of {n} (target <= {n // 5}); address word used: {w} of {n} (target <= ~{round(n / 3)}); address word last: {lw} (target 0)")
    print(f"first token ms: median {statistics.median([x for x in firsts if x is not None] or [0]):.0f}, p90 {pct(firsts, 0.9)}; "
          f"prompt_built median {statistics.median([r['built'] for r in rows if r['built'] is not None] or [0]):.0f}")
    print(f"denied confirms: {state.get('denied', 0)}; total cost ${state['spent']:.4f}")
    print("\n--- Convo B replies ---")
    for r in rows:
        if r["id"].startswith("B"):
            print(f"{r['id']} [{r['q']}] -> {r['reply']}")
    print("\n--- worst replies (most failures, then most sentences) ---")
    for r in sorted(rows, key=lambda r: (len(r["fails"]), r["n"]), reverse=True)[:5]:
        print(f"{r['id']} [{r['q']}] fails={r['fails']} -> {r['reply']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
