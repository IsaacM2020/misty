"""E7: the lesson that is running. One `teach_live` row is the state; lesson.step is the machine; this file is the glue:
start a lesson from a plan, grade his message (keys.grade, never a tool pick), write evidence and slips from the machine's actions,
and build the small result the model words. The model never writes evidence and never sees an answer key before a reveal.
Nothing here knows a subject or a person.
"""
from __future__ import annotations

import json
import re
import secrets

from .. import tutor
from ..errors import Fatal
from ..logs import log_event
from ..openrouter import user_approved
from . import keys, lesson, plan as tplan, slips, sources, spoken
from . import ways as tways
from . import map as tm

WAY_NAMES = tplan.WAYS
PAUSED_KEEP_SECONDS = 12 * 3600          # "carry on" finds a paused lesson for this long
_CARRY = re.compile(r"^(?:ok(?:ay)?[ ,]*)?(?:carry on|go on|keep going|continue|resume|let'?s (?:carry on|continue|go on|keep going)|where were we|go ahead)\W*$", re.I)
_YES = re.compile(r"^(?:yes|yeah|yep|sure|ok(?:ay)?|do that|please|go for it|alright|fine)\b", re.I)
_NO = re.compile(r"^(?:no|nope|nah|not now|skip|don'?t|no thanks)\b", re.I)

TEACH_RULE = """A lesson is running (the teach tool). Each turn the lesson result tells you exactly what to say next: say it in your own voice
and in his ways, one step at a time, calm and short. Never give an answer before he has tried; the only answers you may say are the ones
the result marks as a reveal. Say nothing that is not in the result: no new facts, no extra steps. A hint is only the hint in the result.
Say the question exactly once, at the end. A wrong answer gets one sentence from you before the next way, never a second paragraph.
If the result has narrated, the window is reading the picture aloud: say the idea in your own words and do not read the picture out.
If the result has side_question, answer his question in a sentence or two, then say "Back to ..." and the back_to question again in full. If it has offer, ask whether he wants to do that topic first. If it has finished, say the recap in your words and stop.
Never say the lesson is saved or the score changed: code does that."""


# ---- the row -------------------------------------------------------------------------------------------------------
def _state(r) -> dict:
    x = json.loads(r["extra"] or "{}")
    return {"step": r["step_no"], "phase": r["phase"], "way": r["way_no"], "misses": r["misses"], "hints": r["hints_used"],
            "n": x.get("n", 0), "checks": x.get("checks", []), "wrong": bool(x.get("wrong")), "status": r["status"], "offer": bool(x.get("offer"))}


def _save(ctx, rid: str, s: dict, **extra) -> None:
    old = json.loads(ctx.con.execute("SELECT extra FROM teach_live WHERE id=?", (rid,)).fetchone()["extra"] or "{}")
    old.update(n=s["n"], checks=s["checks"], wrong=s["wrong"], offer=s["offer"], **extra)
    with ctx.con:
        ctx.con.execute("UPDATE teach_live SET step_no=?, phase=?, way_no=?, misses=?, hints_used=?, status=?, updated_at=?, extra=? WHERE id=?",
                        (s["step"], s["phase"], s["way"], s["misses"], s["hints"], s["status"], ctx.clock.now(), json.dumps(old), rid))


def _row(ctx, rid: str):
    return ctx.con.execute("SELECT * FROM teach_live WHERE id=?", (rid,)).fetchone()


def current(ctx):
    """The one live lesson, or None. A lesson left alone for 30 minutes becomes paused here."""
    r = ctx.con.execute("SELECT * FROM teach_live WHERE status='live'").fetchone()
    if r is not None and ctx.clock.now() - r["updated_at"] > lesson.IDLE_PAUSE_SECONDS:
        with ctx.con:
            ctx.con.execute("UPDATE teach_live SET status='paused' WHERE id=?", (r["id"],))
        log_event("TEACH_PAUSED", code="idle")
        return None
    return r


def paused(ctx):
    r = ctx.con.execute("SELECT * FROM teach_live WHERE status='paused' ORDER BY updated_at DESC LIMIT 1").fetchone()
    return r if r is not None and ctx.clock.now() - r["updated_at"] <= PAUSED_KEEP_SECONDS else None


def pending(ctx) -> str | None:
    """Which kind of answering is going on: `lesson`, `drill` or `paper` while one is live, else None."""
    from . import drill, paper
    if current(ctx) is not None:
        return "lesson"
    if drill.current(ctx) is not None:
        return "drill"
    return "paper" if paper.current(ctx) is not None else None


def rule_block(ctx) -> str:
    from . import drill, paper
    if current(ctx) is None and paper.current(ctx) is not None:
        return paper.rule_block(ctx)
    if current(ctx) is None:
        return drill.rule_block(ctx)
    w = tways.block(tways.for_teaching(ctx))
    return TEACH_RULE + ("\n" + w if w else "")


# ---- the plan's items --------------------------------------------------------------------------------------------
def _plan(ctx, r) -> dict:
    p = ctx.con.execute("SELECT plan_json FROM teach_plans WHERE id=?", (r["plan_id"],)).fetchone()
    return json.loads(p["plan_json"]) if p else {}


def _item(pl: dict, phase: str, step: int) -> dict | None:
    if phase == "check":
        return (pl.get("steps") or [{}])[step].get("check") if 0 <= step < len(pl.get("steps") or []) else None
    return pl.get(phase) if phase in ("guided", "solo") else None


def answer_text(key: dict) -> str:
    """The right answer in words, from a key that already passed E6. Only ever said in a reveal."""
    try:
        if "expr" in key:
            v = keys._calc(key["expr"])
            sf = int(key.get("sig_figs") or 3)
            return f"{float(f'{v:.{sf}g}'):g} {key.get('unit') or ''}".strip()
        if "options" in key:
            i = keys._answer_index(key, keys._opts(key) or [])
            return str((keys._opts(key) or [])[i]) if i is not None else ""
        if "accept" in key:
            return str((key["accept"] or [""])[0])
        if "points" in key:
            return "; ".join(str(p.get("text") or "") for p in key["points"])
    except Exception:
        return ""
    return ""


def _needs_open(ctx, topic_id: str) -> dict | None:
    """The first active need of the topic that is not solid yet."""
    now = ctx.clock.now()
    for n in ctx.con.execute("SELECT n.needs_id, t.title FROM teach_needs n JOIN teach_topics t ON t.id=n.needs_id "
                             "WHERE n.topic_id=? AND n.status='active' AND t.status='active' ORDER BY t.created_at", (topic_id,)):
        if tm.level(ctx, n["needs_id"], now) not in ("solid", "mastered"):
            return {"id": n["needs_id"], "title": n["title"]}
    return None


# ---- applying the machine's actions ------------------------------------------------------------------------------
def _evidence(ctx, r, kind: str, phase: str, step: int, seq: int, note: str) -> int | None:
    oid = f"{r['id']}:{seq}:{kind}"
    old = ctx.con.execute("SELECT id FROM teach_evidence WHERE origin='lesson' AND origin_id=? AND kind=?", (oid, kind)).fetchone()
    if old:
        return old["id"]                                       # one answer, one piece of evidence
    return tm.record(ctx, r["topic_id"], kind, "lesson", oid, note)


def _slip(ctx, r, item: dict, his: str, evid_id=None) -> str:
    """E12: the slip keeps the key and the cite, so the question can be asked again word for word. Returns the slip id."""
    sid = "sl_" + secrets.token_hex(6)
    q = str(item.get("q") or "")[:400]
    bq = ctx.con.execute("SELECT id FROM teach_questions WHERE topic_id=? AND q=? ORDER BY made_at LIMIT 1", (r["topic_id"], str(item.get("q") or ""))).fetchone()
    with ctx.con:
        ctx.con.execute("INSERT INTO teach_slips(id, evidence_id, topic_id, at, question, his_answer, right_answer, kind, timed, key_json, cite_json, qid) "
                        "VALUES (?,?,?,?,?,?,?,'',0,?,?,?)",
                        (sid, evid_id, r["topic_id"], ctx.clock.now(), q, str(his or "")[:400], answer_text(item.get("key") or {})[:300],
                         json.dumps(item.get("key") or {}), json.dumps(item.get("cite") or {}), bq["id"] if bq else ""))
    slips.queue_label(ctx, sid)
    return sid


def _build(ctx, r, pl: dict, actions: list, his: str, extra: dict) -> dict:
    """Write evidence and slips, and turn the actions into what the model says. Returns the delivery."""
    steps = pl.get("steps") or []
    do, board = [], None
    seq = int(json.loads(r["extra"] or "{}").get("seq", 0)) + 1
    evid, made = None, []
    for a in actions:
        k = a["do"]
        if k == "evidence":
            evid = _evidence(ctx, r, a["kind"], a["phase"], a["step"], seq, f"{a['phase']} {a['step'] + 1}")
        elif k == "slip":
            it = _item(pl, a["phase"], a["step"])
            if it:
                made.append(_slip(ctx, r, it, his))
        elif k == "explain":
            st = steps[a["step"]]
            w = st["explain"][min(a["way"], 2)]
            do.append({"explain": w["say"], "way": w["way"], **({"idea": st["idea"]} if a["way"] == 0 else {})})
            board = w.get("board") or board
        elif k == "ask":
            it = _item(pl, a["phase"], a["step"])
            lead = {"guided": "Now one with hints allowed.", "solo": "Now one on his own, no hints."}.get(a["phase"], "")
            do.append({"ask": spoken.for_ear(it["q"]) if it else "", **({"note": lead} if lead else {})})
        elif k == "hint":
            it = _item(pl, extra.get("phase", "check"), extra.get("step", 0)) or {}
            hs = it.get("hints") or []
            do.append({"hint": hs[a["n"]] if a["n"] < len(hs) else "think about what the question is really asking"})
        elif k == "reveal":
            it = _item(pl, a["phase"], a["step"]) or {}
            do.append({"reveal": answer_text(it.get("key") or {}), "question": it.get("q", "")})
        elif k == "offer_need":
            n = _needs_open(ctx, r["topic_id"])
            do.append({"offer": n["title"] if n else "the topic before it"})
        elif k == "recap":
            do.append({"recap": pl.get("recap", "")})
        elif k == "finished":
            do.append({"finished": True})
        elif k == "side":
            do.append({"side_question": True})
        elif k == "no_hints":
            do.append({"no_hints": "no hints in this one: give it a go, or he can say show me"})
        elif k == "stopped":
            do.append({"stopped": True})
        elif k == "paused":
            do.append({"paused": True})
    if evid is not None and made:                     # E12: the slip and the evidence from the same answer are one thing, so a flag can cancel it
        with ctx.con:
            for sid in made:
                ctx.con.execute("UPDATE teach_slips SET evidence_id=? WHERE id=? AND evidence_id IS NULL", (evid, sid))
    return {"do": do, "_board": board, "seq": seq}


def _topic_title(ctx, topic_id: str) -> str:
    t = ctx.con.execute("SELECT title FROM teach_topics WHERE id=?", (topic_id,)).fetchone()
    return t["title"] if t else ""


def _result(ctx, r, s: dict, built: dict, verdict: dict | None = None) -> dict:
    do = built["do"]
    if verdict:
        do = [{"verdict": verdict["verdict"], **({"why": verdict["why"]} if verdict.get("why") else {})}] + do
    out = {"ok": True, "lesson": {"topic": _topic_title(ctx, r["topic_id"]), "step": min(s["step"] + 1, max(s["n"], 1)), "of": s["n"],
                                  "phase": s["phase"], "status": s["status"]},
           "do": do, "_board": built.get("_board")}
    return out


def _run(ctx, rid: str, event: dict, his: str = "") -> dict:
    """Apply one event to the lesson row, write what it earns, return the result."""
    r = _row(ctx, rid)
    pl = _plan(ctx, r)
    s0 = _state(r)
    if event.get("e") == "answer" and s0["phase"] == "check":
        event = {**event, "needs_open": _needs_open(ctx, r["topic_id"]) is not None}
    if event.get("e") == "stuck":
        it = _item(pl, s0["phase"], s0["step"]) or {}
        event = {**event, "hints_avail": min(3, len(it.get("hints") or []))}
    s1, acts = lesson.step(s0, event)
    built = _build(ctx, r, pl, acts, his, {"phase": s0["phase"], "step": s0["step"]})
    if s1["status"] == "paused" and s1["offer"]:
        n = _needs_open(ctx, r["topic_id"])
        _save(ctx, rid, s1, seq=built["seq"], need=n["id"] if n else "")
    else:
        _save(ctx, rid, s1, seq=built["seq"])
    if s1["status"] == "done":
        log_event("TEACH_LESSON", code="done")
        try:
            sub = ctx.con.execute("SELECT subject_id FROM teach_topics WHERE id=?", (r["topic_id"],)).fetchone()
            if sub:
                tplan.queue_ready(ctx, sub["subject_id"], limit=2)       # the next topics are ready now: plan them in the background
        except Exception as e:
            log_event("TEACH_PLAN_QUEUE_FAILED", code=type(e).__name__)
    res = _result(ctx, r, s1, built, event.get("_verdict"))
    if s1["status"] == "done":
        try:
            from . import probe
            offer = probe.offer_after(ctx, r["topic_id"], rid)           # E17: three quick ones to see what stuck
            if offer:
                res["probe_offer"] = offer
        except Exception as e:
            log_event("TEACH_PROBE_FAILED", code=type(e).__name__)
    return res


# ---- starting, stopping, resuming ----------------------------------------------------------------------------------
async def start(ctx, topic_id: str) -> dict:
    from . import drill, paper
    if paper.current(ctx) is not None:
        return {"ok": False, "error": "a paper is running: finish or stop the paper first"}
    if drill.current(ctx) is not None:
        return {"ok": False, "error": "a drill is running: finish or stop the drill first"}
    t = ctx.con.execute("SELECT * FROM teach_topics WHERE id=? AND status='active'", (topic_id,)).fetchone()
    if t is None:
        return {"ok": False, "error": "I don't have that topic"}
    p = tplan.get(ctx, topic_id)
    made = False
    if p is None:                                             # no plan yet: make one now (a few seconds, a fraction of a cent)
        with user_approved():
            m = await tplan.make(ctx, topic_id)
        if m["status"] != "usable":
            return {"ok": False, "error": f"I couldn't make a lesson plan for that: {m['why'] or 'the model did not help'}"}
        p, made = tplan.get(ctx, topic_id), True
    for old in ctx.con.execute("SELECT id FROM teach_live WHERE status='live'").fetchall():
        with ctx.con:
            ctx.con.execute("UPDATE teach_live SET status='stopped', updated_at=? WHERE id=?", (ctx.clock.now(), old["id"]))
    steps = p["plan"]["steps"]
    s = lesson.new_state(len(steps), [bool(x.get("check")) for x in steps])
    rid = "lv_" + secrets.token_hex(6)
    now = ctx.clock.now()
    with ctx.con:
        ctx.con.execute("INSERT INTO teach_live(id, topic_id, plan_id, step_no, phase, way_no, misses, hints_used, status, started_at, updated_at, extra) "
                        "VALUES (?,?,?,0,'check',0,0,0,'live',?,?,?)", (rid, topic_id, p["id"], now, now,
                                                                         json.dumps({"n": s["n"], "checks": s["checks"], "wrong": False, "offer": False, "seq": 0})))
    log_event("TEACH_LESSON", code="start")
    res = _run(ctx, rid, {"e": "start"})
    res["first"] = True
    res["plan_made_now"] = made
    if made and tways.for_teaching(ctx):
        res["plan_note"] = "I made this plan your way, so it took a few seconds"
    return res


def stop(ctx) -> dict:
    r = current(ctx) or paused(ctx)
    if r is None:
        return {"ok": False, "error": "no lesson is running"}
    return _run(ctx, r["id"], {"e": "stop"}) | {"stopped": True}


async def resume(ctx) -> dict:
    r = current(ctx)
    if r is not None:
        return {"ok": True, "already": True, **_run(ctx, r["id"], {"e": "again"}, "")}
    r = paused(ctx)
    if r is None:
        return {"ok": False, "error": "no paused lesson to carry on with"}
    x = json.loads(r["extra"] or "{}")
    if x.get("offer"):
        return _run(ctx, r["id"], {"e": "need_no"})
    with ctx.con:
        ctx.con.execute("UPDATE teach_live SET status='paused' WHERE status='live'")
    return _run(ctx, r["id"], {"e": "resume"})


def control(ctx, name: str) -> dict:
    """again | easier | harder on the live lesson (the tool's words for what the classifier also catches)."""
    r = current(ctx)
    if r is None:
        return {"ok": False, "error": "no lesson is running"}
    return _run(ctx, r["id"], {"e": name})


# ---- his message --------------------------------------------------------------------------------------------------
def _side(ctx, r, pl: dict) -> dict:
    res = _run(ctx, r["id"], {"e": "other"})
    s = _state(_row(ctx, r["id"]))
    it = _item(pl, s["phase"], s["step"])
    if it:
        res["do"].append({"then_ask": it["q"]})
        res["back_to"] = {"phase": s["phase"], "step_no": s["step"], "question": it["q"]}
    return res


async def on_message(ctx, text: str) -> dict | None:
    """What a lesson makes of his message before the model runs. None = not for the lesson (or no lesson)."""
    from . import drill, paper
    if paper.current(ctx) is not None:                      # E13: a timed paper stores his message as the answer and says nothing else
        return await paper.on_message(ctx, text)
    r = current(ctx)
    if r is None and (drill.current(ctx) is not None or (paused(ctx) is None and drill.paused(ctx) is not None)):
        return await drill.on_message(ctx, text)            # E11: a drill grades his message the same way
    if r is None:
        p = paused(ctx)
        if p is None:
            from . import probe
            return await probe.on_offer_message(ctx, text)                # E17: his yes or no to a quick check
        x = json.loads(p["extra"] or "{}")
        t = " ".join((text or "").split())
        if x.get("offer") and len(t.split()) <= 6 and (_YES.search(t) or _NO.search(t)):
            if _YES.search(t) and x.get("need"):
                _run(ctx, p["id"], {"e": "need_yes"})
                out = await start(ctx, x["need"])
                if out.get("ok"):
                    out["leaning"] = "this is the topic the other lesson leans on; carry on with the first one after"
                return out
            return _run(ctx, p["id"], {"e": "need_no"})
        if not x.get("offer") and _CARRY.search(t):
            return await resume(ctx)
        return None
    pl = _plan(ctx, r)
    s = _state(r)
    c = lesson.classify(text)
    if c in ("stop", "again", "easier", "harder", "show_me", "stuck"):
        return _run(ctx, r["id"], {"e": c}, text)
    if c == "other" or not tutor.continues_lesson(text):
        return _side(ctx, r, pl)
    it = _item(pl, s["phase"], s["step"])
    if it is None:
        return None
    with user_approved():
        g = await keys.grade(ctx, it["q"], it["key"], text)
    v = g.get("verdict", "wrong")
    return _run(ctx, r["id"], {"e": "answer", "verdict": v, "_verdict": {"verdict": v, "why": g.get("why", "")}}, text)


# ---- the tool -----------------------------------------------------------------------------------------------------
def find_topic(ctx, topic: str = "", subject: str = "") -> dict:
    """Which topic he means: {ok, topic_id} or {ok: False, error}."""
    from . import build as tb
    topic, subject = str(topic or "").strip(), str(subject or "").strip()
    subs = ctx.con.execute("SELECT * FROM teach_subjects WHERE status='active' ORDER BY created_at").fetchall()
    if not subs:
        return {"ok": False, "error": "he has nothing to learn yet: a source and a map first"}
    if subject:
        sub = tb.find_subject(ctx, subject)
        if sub is None:
            return {"ok": False, "error": "I don't have a subject like that"}
        pool = [sub]
    else:
        pool = list(subs)
    if topic:
        hits = []
        for sub in pool:
            try:
                hits.append(tb.find_topic(ctx, sub["id"], topic))
            except Fatal:
                continue
        if len(hits) == 1:
            return {"ok": True, "topic_id": hits[0]["id"]}
        if not hits:
            return {"ok": False, "error": f"I can't find a topic like \"{topic}\" in what he is learning"}
        return {"ok": False, "error": "more than one topic fits: " + ", ".join(h["title"] for h in hits[:4])}
    if len(pool) != 1:
        return {"ok": False, "error": "which subject? " + ", ".join(s["title"] for s in pool[:5])}
    ready = tm.ready(ctx, pool[0]["id"], ctx.clock.now())
    if not ready:
        return {"ok": False, "error": "nothing in that subject is ready to teach yet"}
    return {"ok": True, "topic_id": ready[0]["topic_id"]}


async def _paper_tool(ctx, act: str, args: dict) -> dict:
    from . import build as tb
    from . import paper
    if act == "exam_end":
        return await paper.end(ctx)
    sub_text = str(args.get("subject") or "").strip()
    sub = tb.find_subject(ctx, sub_text)
    if sub_text and sub is None:
        return {"ok": False, "error": "I don't have a subject like that"}
    if sub is None:
        return {"ok": False, "error": "which subject is the paper for?"}
    if args.get("paper") and args.get("scheme"):                    # his words: this paper and this is its mark scheme
        a = paper.pair_by_words(ctx, str(args["paper"]), str(args["scheme"]), sub["id"])
        if not a.get("ok"):
            return a
    return await paper.start(ctx, sub["id"], args.get("minutes"), str(args.get("title") or ""))


async def tool(ctx, args: dict, on_status=None) -> dict:
    act = str(args.get("action") or "")
    if act == "bite":                                               # E15: his answer (later, not_today) to the daily study bite
        from . import bite
        return bite.answer(ctx, str(args.get("answer") or ""))
    if act == "start" and str(args.get("what") or "").strip():     # E9: "teach me this", or a file, link or class
        from . import this
        return await this.start_this(ctx, str(args["what"]), str(args.get("part") or args.get("topic") or ""), on_status)
    if act == "start":
        f = find_topic(ctx, args.get("topic", ""), args.get("subject", ""))
        if f["ok"]:
            from . import probe
            o = probe.offer_before(ctx, f["topic_id"])                      # E17: a topic he has not been taught gets the quick check offered first
            if o is not None and current(ctx) is None and paused(ctx) is None:
                return o
        r = await start(ctx, f["topic_id"]) if f["ok"] else f
        if r.get("ok"):
            from . import bite
            bite.accept(ctx)                                        # a yes to the daily bite
        return r
    if act in ("drill", "level"):                                   # E11 (E12: drill with mine is a review of his own mistakes)
        from . import drill
        r = await drill.tool(ctx, args, on_status)
        if act == "drill" and r.get("ok"):
            from . import bite
            bite.accept(ctx)
        return r
    if act in ("slips", "flag"):                                    # E12
        return slips.tool(ctx, args)
    if act in ("exam_start", "exam_end"):                           # E13 (code says paper: `exam` is her own acceptance exam)
        return await _paper_tool(ctx, act, args)
    if act == "stop":
        from . import drill, paper
        if paper.current(ctx) is not None:
            return await paper.end(ctx)
        if current(ctx) is None and paused(ctx) is None and (drill.current(ctx) is not None or drill.paused(ctx) is not None):
            return drill.stop(ctx)
        if current(ctx) is None and drill.current(ctx) is not None:
            return drill.stop(ctx)
        return stop(ctx)
    if act == "resume":
        from . import drill
        if current(ctx) is None and paused(ctx) is None and drill.paused(ctx) is not None:
            return drill.resume(ctx)
        return await resume(ctx)
    if act in ("again", "easier", "harder"):
        from . import drill
        if current(ctx) is None and drill.current(ctx) is not None:
            return drill.resume(ctx)
        return control(ctx, act)
    return {"ok": False, "error": "that is not something teach can do yet"}
