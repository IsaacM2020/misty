#!/usr/bin/env python3
"""The anybody test: Misty must work for a stranger, with nothing about Isaac anywhere in what she says or is sent.

    engine/.venv/bin/python scripts/checks/anybody.py [--live] [--persona nurse]

A made-up person (tests/engine/personas.py) on an EMPTY throwaway data folder. Two parts:
  1. FREE: render the prompt and the tool list she would send, scan for Isaac's name, subjects and school.
  2. --live (paid, a cent or two): send 10 plain and 5 tool-needing messages through the real chat, scan every reply
     and every request body actually sent.
Each later step adds its own main check as a function at the bottom of this file (STEP_CHECKS).
Known debt that is NOT scanned (SPEC section 8): Singapore time, the memory pipeline's prompts, the bootstrap card.
Exit 1 on any hit. The first run, before card B4, is expected to FAIL: that is the baseline B4 turns green.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

# "Spanish" alone is not forbidden: SYSTEM uses "Spanish words in 15 minutes" as a generic example, not as his subject.
FORBIDDEN = [r"\bIsaac\b", r"\bIGCSE\b", r"\bIB\b", r"Chem, Math, Physics", r"Spanish B", r"Math AA", r"Physics AA", r"Hougang"]
PLAIN = ["hi, how are you", "what's a good way to remember lots of dates", "explain compound interest in two lines",
         "I'm a bit tired today", "what's the difference between weather and climate", "thanks",
         "can you give me a tip for staying focused", "what does the word 'ephemeral' mean",
         "say something encouraging", "who are you and what can you do"]
TOOLY = ["remember that my dentist is Dr Lowe", "what's 18 percent of 250", "I care a lot about my running",
         "what's on my calendar tomorrow", "what did I write about my kitchen plans"]


def scan(label: str, text: str) -> list[str]:
    return [f"{label}: matches {pat}" for pat in FORBIDDEN if re.search(pat, text)]


def setup(persona: str):
    from personas import PERSONAS
    from misty_engine import facts
    ctx, home = _harness.temp_ctx(copy_live=False)
    ctx.paths.wiki.mkdir(parents=True, exist_ok=True)
    about = PERSONAS[persona]["about"]
    (ctx.paths.wiki / "about-isaac.md").write_text(about)
    ctx.settings.set("user_name", about.split(",")[0].strip())          # "Priya": her own name, set the way a person sets it
    ctx.settings.set("user_line", about.split(". ")[0].strip() + ".")
    facts.remember(ctx, "kitchen plans", "The kitchen renovation starts in the spring.")
    return ctx, home


def free_part(ctx) -> list[str]:
    from misty_engine import chat
    fixed, changing = chat.system_parts(ctx, "hello", voice=False, exam=False)
    voice_fixed, _ = chat.system_parts(ctx, "hello", voice=True, exam=False)
    hits = scan("prompt", fixed + changing) + scan("voice prompt", voice_fixed)
    hits += scan("tool list", json.dumps(chat.TOOLS))
    return sorted(set(hits))


async def live_part(ctx) -> tuple[list[str], float]:
    from misty_engine import chat
    sent: list[str] = []
    real_post = ctx.openrouter.post

    async def spy(path, body, **kw):
        sent.append(json.dumps(body, ensure_ascii=False))
        return await real_post(path, body, **kw)
    ctx.openrouter.post = spy
    hits, usd = [], 0.0

    async def quiet(_):
        return None

    async def no(_):
        return False
    for i, text in enumerate(PLAIN + TOOLY):
        out = await chat.send(ctx, text, on_delta=quiet, on_status=quiet, confirm=no, voice=(i % 2 == 1))
        usd += out.get("usd") or 0.0
        hits += scan(f"reply {i + 1}", out.get("reply") or "")
    for i, body in enumerate(sent):
        hits += scan(f"request {i + 1}", body)
    return sorted(set(hits)), usd


# ---- E18: the teacher, for a stranger ------------------------------------------------------------------------------
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "engine" / "fixtures"
QUOTE = "A starter is ready to bake with when it has doubled in size a few hours after feeding and is full of bubbles"
WORDS = ["doubled", "bubbles", "feeding", "ready", "hours", "size", "starter", "bake", "full"]


def _sourdough() -> str:
    """The non-school source: three short paragraphs about a bread starter. Nothing here is a subject of his."""
    rows = json.loads((FIXTURES / "plan_set.json").read_text())
    row = next(r for r in rows if r["id"] == "sourdough" and not r["school"])
    return "\n\n".join(row["paras"])


def _fake_model(seen: list, cites: dict):
    """ask.think replaced: answers by which prompt it is given and records every prompt (system and user) for the scan."""
    from misty_engine.teach import bank, build, match, plan, slips

    def item(q, word):
        return {"q": q, "key": {"accept": [word]}, "hints": ["look at the start", "look at the middle", "the word is in the quote"], "cite": cites["cite"]}

    def a_plan():
        qs = [("What has a ripe starter done to its size?", "doubled"), ("What fills a starter that is ready?", "bubbles"), ("What does it need a few hours before?", "feeding")]
        steps = [{"idea": f"idea {i}", "check": item(q, w),
                  "explain": [{"way": way, "say": f"say {way} {i}", "board": None, "cite": cites["cite"]} for way in plan.WAYS]} for i, (q, w) in enumerate(qs)]
        return {"steps": steps, "guided": item("Which word says it is time to bake?", "ready"), "solo": item("How many hours is the wait?", "hours"), "recap": "the recap words"}

    def nine():
        out = []
        for lv in (1, 2, 3):
            for i in range(3):
                bits = " ".join(f"x{lv}{i}y{j}" for j in range(6))
                out.append({"level": lv, "q": f"Question {bits}: which word fits?", "key": {"accept": [WORDS[(lv * 3 + i) % len(WORDS)]]}, "cite": cites["cite"]})
        return {"questions": out}

    async def think(ctx, system, user, **kw):
        seen.append(system + "\n" + user)
        if system == build.SYSTEM:
            n = cites["chunk"]
            return json.dumps({"topics": [{"key": "feeding", "title": "Feeding a starter", "chunk_no": n, "quote": QUOTE},
                                          {"key": "ready", "title": "When a starter is ready", "chunk_no": n, "quote": QUOTE}], "needs": []}), 0.0
        if system == plan.SYSTEM:
            return json.dumps(a_plan()), 0.0
        if system == bank.SYSTEM:
            return json.dumps(nine()), 0.0
        if system == slips.LABEL_SYSTEM:
            return "slipped", 0.0
        if system == match.SYSTEM:
            return "none", 0.0
        return "{}", 0.0
    return think


async def _teach_run(ctx, seen: list, out: list) -> None:
    """Add the source, map it, make a course, take a lesson (right, wrong, stuck), a drill and a probe. `out` collects every reply."""
    import tempfile
    from misty_engine.teach import ask, bank, course, drill, live, probe, sources
    from misty_engine.teach import map as tm
    src = Path(tempfile.mkdtemp(prefix="misty-anybody-")) / "starter.txt"
    src.write_text(_sourdough())
    added = await sources.add(ctx, str(src))
    out.append(added)
    sid = added["id"]
    chunks = sources.chunks(ctx, sid)
    cites = {"cite": {"source_id": sid, "chunk_no": chunks[0][0], "quote": QUOTE}, "chunk": chunks[0][0]}
    ask.think = _fake_model(seen, cites)
    from misty_engine.teach import build
    subj = tm.add_subject(ctx, "bread", "Bread")["id"]
    out.append(await build.build_map(ctx, sid, subj, merge=False))
    made = course.propose(ctx, subj, "bake a loaf", "in 3 weeks")
    out.append(made)
    out.append(course.activate(ctx, subj))
    topic = ctx.con.execute("SELECT id FROM teach_topics WHERE subject_id=? ORDER BY created_at, id LIMIT 1", (subj,)).fetchone()[0]
    out.append(await bank.make(ctx, topic))

    def right_word() -> str:
        """The right answer to whatever is being asked now: a drill or probe question, else the lesson's question."""
        d = ctx.con.execute("SELECT * FROM teach_drill WHERE status='live'").fetchone()
        if d is not None:
            qid = json.loads(d["qids"])[d["i"]]
            return json.loads(ctx.con.execute("SELECT key_json FROM teach_questions WHERE id=?", (qid,)).fetchone()[0])["accept"][0]
        r = live.current(ctx)
        s = live._state(r)
        return live._item(live._plan(ctx, r), s["phase"], s["step"])["key"]["accept"][0]

    async def say(text: str) -> dict | None:
        res = await live.on_message(ctx, text)
        out.append(res)
        seen.append(live.rule_block(ctx) + drill.rule_block(ctx) + probe.PROBE_RULE)
        return res
    out.append(await live.tool(ctx, {"action": "start", "topic": "Feeding a starter"}))     # offers the quick check first
    await say("yes")
    for ok in (True, False, True):                                             # before: right, wrong, right; then the lesson starts by itself
        await say(right_word() if ok else "banana")
    script = ["right", "wrong", "stuck", "hint", "right"]                      # then right, wrong, stuck, a hint, and the rest right
    for n in range(40):
        if live.current(ctx) is None:
            break
        kind = script[n] if n < len(script) else "right"
        await say({"right": right_word, "wrong": lambda: "banana split", "stuck": lambda: "I'm stuck", "hint": lambda: "give me a hint"}[kind]())
    await say("yes")                                                           # the after check
    for _ in range(3):
        if ctx.con.execute("SELECT 1 FROM teach_drill WHERE status='live'").fetchone():
            await say(right_word())
    out.append(await drill.start(ctx, subj, topic))                            # a drill
    for n in range(8):
        if ctx.con.execute("SELECT 1 FROM teach_drill WHERE status='live'").fetchone() is None:
            break
        await say(right_word() if n != 1 else "banana")
    out.append(probe.line(ctx, topic))
    from misty_engine import chat
    for voice in (False, True):
        fixed, changing = chat.system_parts(ctx, "teach me this", voice=voice, exam=False)
        seen.append(fixed + changing)
    seen.append(json.dumps(chat.TOOLS))


def check_teach(ctx) -> list[str]:
    """E18: the teacher works for a stranger. Fake model replies only. Every reply, prompt and tool text is scanned for the FORBIDDEN list."""
    from misty_engine.teach import ask
    real = ask.think
    seen: list = []
    out: list = []
    fails: list[str] = []
    try:
        asyncio.run(_teach_run(ctx, seen, out))
    except Exception as e:                                               # a crash is a failure of the test, say where
        fails.append(f"teach: the run stopped: {type(e).__name__}: {e}")
    finally:
        ask.think = real
    ran = [o for o in out if o]
    if not fails and len(ran) < 12:
        fails.append(f"teach: only {len(ran)} steps produced anything")
    for i, text in enumerate(seen):
        fails += scan(f"teach prompt {i + 1}", text)
    for i, o in enumerate(out):
        fails += scan(f"teach reply {i + 1}", json.dumps(o, ensure_ascii=False, default=str))
    return sorted(set(fails))


# Each later step appends one function here: (ctx) -> list of failures. Keep them cheap.
STEP_CHECKS: list = [check_teach]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--persona", default="nurse")
    a = ap.parse_args()
    ctx, home = setup(a.persona)
    try:
        hits = free_part(ctx)
        print(f"anybody (free): {'PASS' if not hits else 'FAIL'}, {len(hits)} place(s) name him")
        for h in hits:
            print("  ", h)
        usd = 0.0
        if a.live:
            from misty_engine.openrouter import user_approved
            with user_approved():
                live_hits, usd = asyncio.run(live_part(ctx))
            print(f"anybody (live, 15 messages): {'PASS' if not live_hits else 'FAIL'}, {len(live_hits)} hit(s), ${usd:.4f}")
            for h in live_hits[:30]:
                print("  ", h)
            hits += live_hits
        for fn in STEP_CHECKS:
            hits += fn(ctx)
    finally:
        _harness.cleanup(home)
    return 0 if not hits else 1


if __name__ == "__main__":
    sys.exit(main())
