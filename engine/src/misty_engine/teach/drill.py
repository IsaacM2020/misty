"""E11: the drill that is running. A drill is a test, not a lesson: 5 questions (3 for "quick") on the one topic that most needs it,
no hints, graded in code (keys.grade), evidence written from the machine's actions. One `teach_drill` row is the whole state;
`step(state, event)` is the pure machine; the rest is glue, picking and the two read tools. The model only words results.
Nothing here knows a subject or a person.
"""
from __future__ import annotations

import json
import re
import secrets

from .. import tutor
from ..logs import log_event
from ..openrouter import user_approved
from . import bank, keys, lesson, probe, slips, spoken
from . import map as tm

LENGTH, QUICK = 5, 3
MIN_BANK = 3
PAUSED_KEEP_SECONDS = 12 * 3600
_CARRY = re.compile(r"^(?:ok(?:ay)?[ ,]*)?(?:carry on|go on|keep going|continue|resume|let'?s (?:carry on|continue|go on|keep going)|where were we|go ahead)\W*$", re.I)

DRILL_RULE = """A drill is running (the teach tool). It is a test, not a lesson: no hints and no teaching. Each turn the result tells you exactly what to say:
say it in your own voice, short. Say the verdict in a few words, then the answer only when the result has a reveal, then the next question exactly once, at the end.
Never give an answer before he has tried; the only answers you may say are the ones marked reveal. If the result has side_question, answer it in a sentence or two,
then say "Back to ..." and the back_to question in full. If it has finished, say how many he got right out of the total and stop. Never say the score changed: code does that."""


# ---- the pure machine ----------------------------------------------------------------------------------------------
def new_state(n: int) -> dict:
    return {"i": 0, "n": n, "right": 0, "wrong": 0, "partly": 0, "status": "live"}


def _advance(s: dict, acts: list) -> None:
    if s["i"] + 1 >= s["n"]:
        s["status"] = "done"
        acts.append({"do": "finished"})
    else:
        s["i"] += 1
        acts.append({"do": "ask", "i": s["i"]})


def step(state: dict, event: dict) -> tuple[dict, list]:
    s, acts, e = dict(state), [], event.get("e")
    if s["status"] in ("done", "stopped"):
        return s, acts
    if e == "stop":
        s["status"] = "stopped"
        acts.append({"do": "stopped"})
    elif e == "pause":
        s["status"] = "paused"
        acts.append({"do": "paused"})
    elif e in ("start", "resume", "again", "easier", "harder"):
        s["status"] = "live"
        acts.append({"do": "ask", "i": s["i"]})
    elif s["status"] != "live":
        return s, acts
    elif e == "other":
        acts.append({"do": "side"})
    elif e == "answer":
        v = event.get("verdict")
        if v == "right":
            s["right"] += 1
            acts.append({"do": "evidence", "kind": "right_alone", "i": s["i"]})
        elif v == "partly":
            s["partly"] += 1
            acts.append({"do": "evidence", "kind": "partly", "i": s["i"]})
        else:
            s["wrong"] += 1
            acts += [{"do": "evidence", "kind": "wrong", "i": s["i"]}, {"do": "slip", "i": s["i"]}, {"do": "reveal", "i": s["i"]}]
        _advance(s, acts)
    elif e in ("show_me", "stuck"):                       # a hint request in a test shows the answer and counts as wrong
        s["wrong"] += 1
        acts += [{"do": "evidence", "kind": "wrong", "i": s["i"]}, {"do": "slip", "i": s["i"]}, {"do": "reveal", "i": s["i"]}]
        _advance(s, acts)
    return s, acts


# ---- the row -------------------------------------------------------------------------------------------------------
def _row(ctx, rid: str):
    return ctx.con.execute("SELECT * FROM teach_drill WHERE id=?", (rid,)).fetchone()


def _state(r) -> dict:
    x = json.loads(r["extra"] or "{}")
    return {"i": r["i"], "n": len(json.loads(r["qids"])), "right": r["right"], "wrong": r["wrong"], "partly": x.get("partly", 0), "status": r["status"]}


def _save(ctx, rid: str, s: dict) -> None:
    r = _row(ctx, rid)
    x = json.loads(r["extra"] or "{}")
    x["partly"] = s["partly"]
    with ctx.con:
        ctx.con.execute("UPDATE teach_drill SET i=?, right=?, wrong=?, status=?, updated_at=?, extra=? WHERE id=?",
                        (s["i"], s["right"], s["wrong"], s["status"], ctx.clock.now(), json.dumps(x), rid))


def current(ctx):
    """The one live drill, or None. A drill left alone for 30 minutes becomes paused here."""
    r = ctx.con.execute("SELECT * FROM teach_drill WHERE status='live'").fetchone()
    if r is not None and ctx.clock.now() - r["updated_at"] > lesson.IDLE_PAUSE_SECONDS:
        with ctx.con:
            ctx.con.execute("UPDATE teach_drill SET status='paused' WHERE id=?", (r["id"],))
        log_event("TEACH_PAUSED", code="idle_drill")
        return None
    return r


def paused(ctx):
    r = ctx.con.execute("SELECT * FROM teach_drill WHERE status='paused' ORDER BY updated_at DESC LIMIT 1").fetchone()
    return r if r is not None and ctx.clock.now() - r["updated_at"] <= PAUSED_KEEP_SECONDS else None


def rule_block(ctx) -> str:
    cur = current(ctx)
    if cur is None:
        return ""
    if ctx.con.execute("SELECT 1 FROM teach_drill WHERE status='live' AND kind='probe'").fetchone():   # E17: a quick check is quiet
        return probe.PROBE_RULE
    from . import ways as tways
    w = tways.block(tways.for_teaching(ctx))
    return DRILL_RULE + ("\n" + w if w else "")


def _q(ctx, r, i: int):
    qids = json.loads(r["qids"])
    if not 0 <= i < len(qids):
        return None
    return ctx.con.execute("SELECT * FROM teach_questions WHERE id=?", (qids[i],)).fetchone()


# ---- picking -------------------------------------------------------------------------------------------------------
def levels_for(level: str) -> list[int]:
    return {"unknown": [1], "seen": [1], "shaky": [1, 2], "solid": [2, 3], "mastered": [3]}.get(level, [1])


def _bank_usable(ctx, topic_id: str) -> int:
    return bank.usable(ctx, topic_id)


def _topics(ctx, subject_id):
    q, a = "SELECT * FROM teach_topics WHERE status='active'", []
    if subject_id:
        q, a = q + " AND subject_id=?", [subject_id]
    return ctx.con.execute(q + " ORDER BY created_at, id", a).fetchall()


def pick_topic(ctx, subject_id=None) -> dict:
    """The topic that most needs a drill: due ones first (most overdue first), then the weakest, then the rest by score.
    A topic never taught is not drilled, and one with fewer than 3 usable bank questions is skipped (and queued to be made)."""
    now = ctx.clock.now()
    cands = []
    for t in _topics(ctx, subject_id):
        lv = tm.level(ctx, t["id"], now)
        if lv == "unknown":
            continue
        d = tm.due(ctx, t["id"], now)
        cands.append((0 if d is not None and d <= now else 1, d if d is not None and d <= now else 0.0, tm.score(ctx, t["id"], now), t))
    cands.sort(key=lambda c: (c[0], c[1], c[2], c[3]["created_at"]))
    queued = 0
    for _, _, _, t in cands:
        if _bank_usable(ctx, t["id"]) >= MIN_BANK:
            return {"topic_id": t["id"], "title": t["title"]}
        if queued < 2:
            try:
                bank.queue_bank(ctx, t["id"])
                queued += 1
            except Exception as e:
                log_event("TEACH_BANK_QUEUE_FAILED", code=type(e).__name__)
    if cands:
        return {"topic_id": None, "error": "I need to make questions first, back in a minute", "queued": queued}
    return {"topic_id": None, "error": "nothing is ready to drill yet: learn a topic first"}


def _needs(ctx, topic_id: str) -> list[str]:
    return [r["needs_id"] for r in ctx.con.execute(
        "SELECT n.needs_id FROM teach_needs n JOIN teach_topics t ON t.id=n.needs_id WHERE n.topic_id=? AND n.status='active' AND t.status='active' "
        "ORDER BY t.created_at", (topic_id,))]


def pick_questions(ctx, topic_id: str, n: int) -> list[str]:
    """n question ids from the topic, plus its direct needs when the topic is shaky or worse."""
    now = ctx.clock.now()
    lv = tm.level(ctx, topic_id, now)
    own: list[dict] = []
    for level in levels_for(lv):
        own += bank.pick(ctx, topic_id, level, n, exclude={q["id"] for q in own}, now=now, allow_plan=True)
    back: list[dict] = []
    if lv in ("unknown", "seen", "shaky"):
        for nid in _needs(ctx, topic_id):
            nl = tm.level(ctx, nid, now)
            if nl in ("solid", "mastered"):
                continue
            for level in levels_for(nl):
                back += bank.pick(ctx, nid, level, 1, exclude={q["id"] for q in back}, now=now, allow_plan=True)
                break
        back = back[:2]
    take_back = back[:min(len(back), max(0, n - 3))] if back else []
    chosen = own[:n - len(take_back)] + take_back
    if len(chosen) < n:                                    # too few at those levels: fill from any usable bank question of the topic
        for r in ctx.con.execute("SELECT * FROM teach_questions WHERE topic_id=? AND status='usable' ORDER BY uses, COALESCE(last_used,0), id", (topic_id,)):
            if r["id"] not in {q["id"] for q in chosen} and (r["source"] == "bank"):
                chosen.append(dict(r))
            if len(chosen) >= n:
                break
    return [q["id"] for q in chosen][:n]


# ---- applying the machine's actions ------------------------------------------------------------------------------
def _evidence(ctx, r, kind: str, i: int) -> int | None:
    oid = f"{r['id']}:{i}"
    old = ctx.con.execute("SELECT id FROM teach_evidence WHERE origin='drill' AND origin_id=?", (oid,)).fetchone()
    if old:
        return old["id"]                                       # one answer, one piece of evidence
    q = _q(ctx, r, i)
    topic = q["topic_id"] if q is not None else r["topic_id"]
    note = "probe:" + json.loads(r["extra"] or "{}").get("set", "") if r["kind"] == "probe" else f"drill question {i + 1}"
    return tm.record(ctx, topic, kind, "drill", oid, note)


def _slip(ctx, r, i: int, his: str, evid) -> None:
    from . import live
    q = _q(ctx, r, i)
    if q is None:
        return
    sid = "sl_" + secrets.token_hex(6)
    with ctx.con:
        ctx.con.execute("INSERT INTO teach_slips(id, evidence_id, topic_id, at, question, his_answer, right_answer, kind, timed, key_json, cite_json, qid) "
                        "VALUES (?,?,?,?,?,?,?,'',0,?,?,?)",
                        (sid, evid, q["topic_id"], ctx.clock.now(), q["q"][:400], str(his or "")[:400],
                         live.answer_text(json.loads(q["key_json"] or "{}"))[:300], q["key_json"] or "", q["cite_json"] or "", q["id"]))
    slips.queue_label(ctx, sid)


def _build(ctx, r, acts: list, his: str) -> list:
    do, evid = [], None
    for a in acts:
        k = a["do"]
        if k == "evidence":
            evid = _evidence(ctx, r, a["kind"], a["i"])
            if r["kind"] == "review":                             # E12: a right answer twice in a row retires the question from the review
                rq = _q(ctx, r, a["i"])
                if rq is not None:
                    slips.on_review_answer(ctx, rq["id"], a["kind"])
        elif k == "slip":
            if r["kind"] != "probe":                                  # E17: a quick check keeps no mistakes book
                _slip(ctx, r, a["i"], his, evid)
        elif k == "reveal":
            if r["kind"] == "probe":                                  # and never shows the answer
                continue
            from . import live
            q = _q(ctx, r, a["i"])
            do.append({"reveal": live.answer_text(json.loads(q["key_json"] or "{}")) if q is not None else "", "question": q["q"] if q is not None else ""})
        elif k == "ask":
            q = _q(ctx, r, a["i"])
            if q is not None:
                bank.mark_used(ctx, q["id"], ctx.clock.now())
                do.append({"ask": spoken.for_ear(q["q"])})
        elif k == "side":
            do.append({"side_question": True})
        elif k == "finished":
            do.append({"finished": True})
        elif k == "stopped":
            do.append({"stopped": True})
        elif k == "paused":
            do.append({"paused": True})
    return do


def _topic_title(ctx, topic_id: str) -> str:
    t = ctx.con.execute("SELECT title FROM teach_topics WHERE id=?", (topic_id,)).fetchone()
    return t["title"] if t else ""


def _run(ctx, rid: str, event: dict, his: str = "") -> dict:
    r = _row(ctx, rid)
    s0 = _state(r)
    s1, acts = step(s0, event)
    do = _build(ctx, r, acts, his)
    _save(ctx, rid, s1)
    if event.get("_verdict"):
        why = event["_verdict"].get("why") if r["kind"] != "probe" else ""
        do = [{"verdict": event["_verdict"]["verdict"], **({"why": why} if why else {})}] + do
    if s1["status"] in ("done", "stopped"):
        log_event("TEACH_DRILL", code=s1["status"], n=s1["right"])
    out = {"ok": True, "drill": {"topic": "your mistakes" if r["kind"] == "review" else _topic_title(ctx, r["topic_id"]), "question": min(s1["i"] + 1, s1["n"]), "of": s1["n"], "status": s1["status"]}, "do": do}
    if r["kind"] == "probe" and s1["status"] in ("done", "stopped"):
        out["probe"] = probe.finish(ctx, r, s1)
    if s1["status"] == "stopped":
        out["stopped"] = True
    if s1["status"] == "done":
        out["drill"]["right"], out["drill"]["wrong"], out["drill"]["partly"] = s1["right"], s1["wrong"], s1["partly"]
    return out


# ---- starting, stopping, resuming ----------------------------------------------------------------------------------
async def start(ctx, subject_id=None, topic_id=None, quick: bool = False, mine: bool = False) -> dict:
    from . import live, paper
    if paper.current(ctx) is not None:
        return {"ok": False, "error": "a paper is running: finish or stop the paper first"}
    if live.current(ctx) is not None:
        return {"ok": False, "error": "a lesson is running: finish or stop the lesson first"}
    if current(ctx) is not None:
        return {"ok": False, "error": "a drill is already running: finish or stop it first"}
    if mine:                                                       # E12: the weekly review, only his own mistakes asked again word for word
        qids = slips.review_qids(ctx, subject_id, QUICK if quick else LENGTH)
        if len(qids) < slips.MIN_REVIEW:
            return {"ok": False, "error": f"I only have {len(qids)} of your mistakes saved with their answers, and I need {slips.MIN_REVIEW} to review"}
        return _begin(ctx, subject_id or "", "", qids, "review")
    if topic_id is None:
        pk = pick_topic(ctx, subject_id)
        if pk["topic_id"] is None:
            return {"ok": False, "error": pk["error"]}
        topic_id = pk["topic_id"]
    fp = probe.for_drill(ctx, topic_id)                             # E17: an `after` left for the day, or the `week` one, is this drill
    if fp is not None:
        return _begin(ctx, _subject_of(ctx, topic_id), topic_id, fp[2], "probe", {"set": fp[0], "probe_id": fp[1]})
    n = QUICK if quick else LENGTH
    qids = pick_questions(ctx, topic_id, n)
    if len(qids) < MIN_BANK:
        return {"ok": False, "error": "I need to make questions first, back in a minute"}
    return _begin(ctx, _subject_of(ctx, topic_id), topic_id, qids, "drill")


def _subject_of(ctx, topic_id: str) -> str:
    sub = ctx.con.execute("SELECT subject_id FROM teach_topics WHERE id=?", (topic_id,)).fetchone()
    return sub["subject_id"] if sub else ""


def begin_probe(ctx, topic_id: str, set_name: str, qids: list, then_topic: str = "", probe_id: str = "") -> dict:
    """E17: run a probe set as a drill. The guard runs again here, before anything is asked."""
    from . import live, paper
    if paper.current(ctx) is not None or live.current(ctx) is not None or current(ctx) is not None:
        return {"ok": False, "error": "something else is running: finish or stop it first"}
    if not probe.valid(ctx, topic_id, set_name, qids):
        return {"ok": False, "error": "that quick check cannot be made from the bank"}
    return _begin(ctx, _subject_of(ctx, topic_id), topic_id, qids, "probe", {"set": set_name, "then": then_topic, "probe_id": probe_id})


def _begin(ctx, subject_id: str, topic_id: str, qids: list, kind: str, extra: dict | None = None) -> dict:
    for old in ctx.con.execute("SELECT id FROM teach_drill WHERE status='paused'").fetchall():
        with ctx.con:
            ctx.con.execute("UPDATE teach_drill SET status='stopped' WHERE id=?", (old["id"],))
    rid = "dr_" + secrets.token_hex(6)
    now = ctx.clock.now()
    with ctx.con:
        ctx.con.execute("INSERT INTO teach_drill(id, subject_id, topic_id, kind, status, qids, i, right, wrong, extra, started_at, updated_at) "
                        "VALUES (?,?,?,?,'live',?,0,0,0,?,?,?)", (rid, subject_id, topic_id, kind, json.dumps(qids), json.dumps(extra or {}), now, now))
    log_event("TEACH_DRILL", code="start_review" if kind == "review" else "start_probe" if kind == "probe" else "start")
    res = _run(ctx, rid, {"e": "start"})
    res["first"] = True
    return res


def stop(ctx) -> dict | None:
    r = current(ctx) or paused(ctx)
    return _run(ctx, r["id"], {"e": "stop"}) if r is not None else None


def resume(ctx) -> dict:
    r = current(ctx)
    if r is not None:
        return {"ok": True, "already": True, **_run(ctx, r["id"], {"e": "again"})}
    r = paused(ctx)
    if r is None:
        return {"ok": False, "error": "no paused drill to carry on with"}
    return _run(ctx, r["id"], {"e": "resume"})


# ---- his message --------------------------------------------------------------------------------------------------
async def on_message(ctx, text: str) -> dict | None:
    """What a drill makes of his message before the model runs. None = not for the drill (or no drill)."""
    r = current(ctx)
    if r is None:
        p = paused(ctx)
        if p is not None and _CARRY.search(" ".join((text or "").split())):
            return resume(ctx)
        return None
    c = lesson.classify(text)
    if c in ("stop", "again", "easier", "harder", "show_me", "stuck"):
        return await _after_probe(ctx, _run(ctx, r["id"], {"e": c}, text))
    if c == "other" or not tutor.continues_lesson(text):
        res = _run(ctx, r["id"], {"e": "other"})
        s = _state(_row(ctx, r["id"]))
        q = _q(ctx, _row(ctx, r["id"]), s["i"])
        if q is not None:
            res["do"].append({"then_ask": q["q"]})
            res["back_to"] = {"question_no": s["i"], "question": q["q"]}
        return res
    q = _q(ctx, r, r["i"])
    if q is None:
        return None
    with user_approved():
        g = await keys.grade(ctx, q["q"], json.loads(q["key_json"] or "{}"), text)
    v = g.get("verdict", "wrong")
    return await _after_probe(ctx, _run(ctx, r["id"], {"e": "answer", "verdict": v, "_verdict": {"verdict": v, "why": g.get("why", "")}}, text))


async def _after_probe(ctx, res: dict) -> dict:
    """E17: a `before` probe that ended well starts its lesson by itself (extra.then). The last verdict stays in the probe result."""
    then = (res.get("probe") or {}).pop("then", "")
    if then:
        from . import live
        lr = await live.start(ctx, then)
        res["lesson_started"] = lr
    return res


# ---- the tool: drill and level --------------------------------------------------------------------------------------
def _why(ctx, topic_id: str, now: float) -> str:
    r = ctx.con.execute("SELECT kind, at FROM teach_evidence WHERE topic_id=? AND weight<0 AND note NOT LIKE 'cancelled%' ORDER BY at DESC, id DESC LIMIT 1", (topic_id,)).fetchone()
    if r is None:
        return "not much evidence yet"
    days = int((now - r["at"]) // 86400)
    when = "today" if days <= 0 else f"{days} day{'s' if days != 1 else ''} ago"
    return {"wrong": f"you got one wrong {when}", "class_lost": f"your class notes said you got lost on it {when}",
            "asked_help": f"you asked for help on it {when}"}.get(r["kind"], f"a bad sign {when}")


def level(ctx, args: dict) -> dict:
    """Read-only: the weakest 3 topics with a plain reason, and what is due. Never opens a drill."""
    from . import build as tb
    from . import live
    now = ctx.clock.now()
    sub = None
    if str(args.get("subject") or "").strip():
        sub = tb.find_subject(ctx, str(args["subject"]))
        if sub is None:
            return {"ok": False, "error": "I don't have a subject like that"}
    if str(args.get("topic") or "").strip():
        f = live.find_topic(ctx, str(args["topic"]), str(args.get("subject") or ""))
        if not f["ok"]:
            return f
        t = ctx.con.execute("SELECT * FROM teach_topics WHERE id=?", (f["topic_id"],)).fetchone()
        d = tm.due(ctx, t["id"], now)
        return {"ok": True, "topic": {"topic": t["title"], "level": tm.level(ctx, t["id"], now), "why": _why(ctx, t["id"], now),
                                      "due": bool(d is not None and d <= now)}}
    subs = [sub] if sub is not None else ctx.con.execute("SELECT * FROM teach_subjects WHERE status='active'").fetchall()
    if not subs:
        return {"ok": False, "error": "he has nothing to learn yet"}
    weak = []
    for s in subs:
        weak += tm.weakest(ctx, s["id"], 3, now)
    weak.sort(key=lambda d: (round(d["score"], 9), -d["last_bad"]))
    due = []
    for s in subs:
        for t in tm._topics(ctx, s["id"]):
            d = tm.due(ctx, t["id"], now)
            if d is not None and d <= now:
                due.append(t["title"])
    return {"ok": True, "weakest": [{"topic": w["title"], "level": "shaky", "why": _why(ctx, w["topic_id"], now)} for w in weak[:3]], "due": due[:6]}


async def tool(ctx, args: dict, on_status=None) -> dict:
    from . import build as tb
    act = str(args.get("action") or "")
    if act == "drill":
        sub = None
        if str(args.get("subject") or "").strip():
            sub = tb.find_subject(ctx, str(args["subject"]))
            if sub is None:
                return {"ok": False, "error": "I don't have a subject like that"}
        tid = None
        if str(args.get("topic") or "").strip() and not args.get("mine"):       # E15: a drill on the topic of the day's bite
            from . import live
            f = live.find_topic(ctx, str(args["topic"]), str(args.get("subject") or ""))
            if not f["ok"]:
                return f
            tid = f["topic_id"]
        return await start(ctx, sub["id"] if sub is not None else None, topic_id=tid, quick=bool(args.get("quick")), mine=bool(args.get("mine")))
    if act == "level":
        return level(ctx, args)
    return {"ok": False, "error": "that is not something the drill can do"}
