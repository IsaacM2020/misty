"""E12: the mistakes book. Fake model replies only, no paid call. A made-up stranger's subject."""
import asyncio
import itertools
import json
import random

import pytest

from misty_engine import gate
from misty_engine.teach import bank, drill, live, plan, slips, sources
from misty_engine.teach import map as tm

DAY = 86400
PASSAGE = "A negative split is when the second half of a run is faster than the first half of the run."
_ids = itertools.count(1)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def world(fctx):
    src = sources._keep(fctx, "page", "https://x.test/run", "run", "x")["id"]
    s = tm.add_subject(fctx, "running", "Running")["id"]
    t = {k: tm.add_topic(fctx, s, k, k.title(), {"source_id": src, "chunk_no": 0, "quote": k})["id"] for k in ("pacing", "fuel")}
    return fctx, s, t, src


def add_slip(ctx, topic, q=None, kind="", at=None, timed=0, key=True, evid=None, qid="", rights=0, reviewed_at=None, flagged=0, word=None):
    n = next(_ids)
    q = q or f"Slip question number {n} about pacing?"
    word = word or f"ans{n}"
    sid = f"sl_t{n}"
    ctx.con.execute("INSERT INTO teach_slips(id, evidence_id, topic_id, at, question, his_answer, right_answer, kind, timed, key_json, cite_json, flagged, reviewed_at, qid, rights) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, evid, topic, at if at is not None else ctx.clock.now(), q, "banana", word, kind, timed,
                     json.dumps({"accept": [word]}) if key else "", "{}", flagged, reviewed_at, qid, rights))
    return sid


def add_q(ctx, topic, word, level=1, source="bank", status="usable"):
    n = next(_ids)
    qid = f"qq{n}"
    ctx.con.execute("INSERT INTO teach_questions(id, topic_id, level, q, key_json, cite_json, source, status, made_at, uses) VALUES (?,?,?,?,?,?,?,?,?,0)",
                    (qid, topic, level, f"Bank question {n}?", json.dumps({"accept": [word]}), "{}", source, status, ctx.clock.now()))
    return qid


# ---- the table -------------------------------------------------------------------------------------------------------
def test_migration_adds_the_columns(fctx):
    cols = {r[1] for r in fctx.con.execute("pragma table_info(teach_slips)")}
    assert {"key_json", "cite_json", "flagged", "reviewed_at", "qid", "rights"} <= cols
    assert fctx.con.execute("select count(*) from teach_patterns_said").fetchone()[0] == 0


# ---- the label --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reply,kind", [("didnt_know", "didnt_know"), ("slipped", "slipped"), ("wrong_method", "wrong_method"), ("misread", "misread"),
                                         (" Slipped. ", "slipped"), ("unsure", ""), ("I think he misread it", ""), ("banana", ""), ("", "")])
def test_label_stores_one_of_the_four_words_and_junk_as_empty(world, fake_or, reply, kind):
    ctx, s, t, _ = world
    sid = add_slip(ctx, t["pacing"])
    fake_or.chat_returns(reply)
    assert run(slips.label(ctx, sid)) == kind
    assert ctx.con.execute("select kind from teach_slips where id=?", (sid,)).fetchone()[0] == kind


def test_label_call_is_cheap_and_plain(world, fake_or):
    ctx, s, t, _ = world
    sid = add_slip(ctx, t["pacing"])
    fake_or.chat_returns("slipped")
    run(slips.label(ctx, sid))
    b = fake_or.chat_bodies[0]
    assert b["temperature"] == 0 and b["reasoning"].get("enabled") is not True
    assert "Slip question number" in b["messages"][-1]["content"] and "banana" in b["messages"][-1]["content"]


def test_a_wrong_drill_answer_keeps_the_key_and_queues_a_label(world):
    ctx, s, t, _ = world
    for lv in (1, 2, 3):
        for _i in range(3):
            add_q(ctx, t["pacing"], f"w{next(_ids)}", lv)
    tm.record(ctx, t["pacing"], "wrong", "drill", "seed")
    run(drill.start(ctx, s, t["pacing"]))
    run(drill.on_message(ctx, "banana"))
    sl = ctx.con.execute("select * from teach_slips").fetchall()
    assert len(sl) == 1 and json.loads(sl[0]["key_json"])["accept"] and sl[0]["qid"].startswith("qq") and sl[0]["evidence_id"]
    jobs = ctx.con.execute("select kind, payload from jobs where kind='teach_slip_label'").fetchall()
    assert len(jobs) == 1 and json.loads(jobs[0]["payload"])["slip_id"] == sl[0]["id"]


def test_a_wrong_lesson_answer_keeps_the_key_and_links_the_evidence(world):
    ctx, s, t, src = world
    ctx.con.execute("DELETE FROM teach_chunks WHERE source_id=?", (src,))
    ctx.con.execute("INSERT INTO teach_chunks(source_id, chunk_no, text_scrubbed) VALUES (?,0,?)", (src, PASSAGE))
    cite = {"source_id": src, "chunk_no": 0, "quote": PASSAGE[:60]}
    item = lambda q, w: {"q": q, "key": {"accept": [w]}, "hints": ["a", "b", "c"], "cite": cite}
    steps = [{"idea": f"i{i}", "check": item(f"Question {i}?", f"ans{i}"),
              "explain": [{"way": w, "say": f"say {w}", "board": None, "cite": cite} for w in plan.WAYS]} for i in range(3)]
    pl = {"steps": steps, "guided": item("G?", "g"), "solo": item("S?", "s"), "recap": "r"}
    ctx.con.execute("INSERT INTO teach_plans(id, topic_id, version, plan_json, model, cost, status, why, made_at) VALUES ('tp1',?,1,?,'h',0,'usable','',?)",
                    (t["pacing"], json.dumps(pl), ctx.clock.now()))
    run(live.start(ctx, t["pacing"]))
    run(live.on_message(ctx, "banana"))
    sl = ctx.con.execute("select * from teach_slips").fetchone()
    assert json.loads(sl["key_json"]) == {"accept": ["ans0"]} and sl["evidence_id"] is not None and sl["cite_json"]
    assert ctx.con.execute("select kind from teach_evidence where id=?", (sl["evidence_id"],)).fetchone()[0] == "wrong"


# ---- the tool: slips -------------------------------------------------------------------------------------------------
def test_slips_lists_newest_first_with_counts_and_plain_line(world):
    ctx, s, t, _ = world
    now = ctx.clock.now()
    add_slip(ctx, t["pacing"], "old one?", "slipped", at=now - 3 * DAY)
    add_slip(ctx, t["pacing"], "mid one?", "didnt_know", at=now - 1 * DAY)
    add_slip(ctx, t["fuel"], "new one?", "", at=now - 100)
    r = slips.tool(ctx, {"action": "slips"})
    assert r["ok"] and r["total"] == 3 and [x["question"] for x in r["recent"]] == ["new one?", "mid one?", "old one?"]
    assert r["by_kind"] == {"slipped": 1, "didnt_know": 1, "not filed yet": 1} and r["by_subject"] == {"Running": 3}
    assert "3 mistakes" in r["say"]


def test_slips_since_and_subject(world):
    ctx, s, t, _ = world
    now = ctx.clock.now()
    add_slip(ctx, t["pacing"], at=now - 10 * DAY)
    add_slip(ctx, t["pacing"], at=now - 100)
    assert slips.tool(ctx, {"action": "slips", "since": "today"})["total"] == 1
    assert slips.tool(ctx, {"action": "slips", "since": "this week"})["total"] == 1
    assert slips.tool(ctx, {"action": "slips", "since": "last 30 days"})["total"] == 2
    assert slips.tool(ctx, {"action": "slips", "since": "2000-01-01"})["total"] == 2
    assert not slips.tool(ctx, {"action": "slips", "since": "blue"})["ok"]
    assert slips.tool(ctx, {"action": "slips", "subject": "running"})["total"] == 2
    assert not slips.tool(ctx, {"action": "slips", "subject": "knitting"})["ok"]
    r = slips.tool(ctx, {"action": "slips", "since": "today"})
    assert "mistake" in r["say"]


def test_slips_with_nothing_is_plain(world):
    r = slips.tool(world[0], {"action": "slips"})
    assert r["ok"] and r["total"] == 0 and "No mistakes" in r["say"]


def test_slips_makes_no_model_call(world, fake_or):
    ctx, s, t, _ = world
    add_slip(ctx, t["pacing"])
    slips.tool(ctx, {"action": "slips"})
    assert fake_or.chat_bodies == []


# ---- the review ------------------------------------------------------------------------------------------------------
def test_review_needs_three_slips_with_keys(world):
    ctx, s, t, _ = world
    add_slip(ctx, t["pacing"]); add_slip(ctx, t["pacing"]); add_slip(ctx, t["pacing"], key=False)
    r = run(drill.start(ctx, s, mine=True))
    assert not r["ok"] and "2 of your mistakes" in r["error"]
    assert ctx.con.execute("select count(*) from teach_drill").fetchone()[0] == 0


def test_review_old_flagged_and_keyless_slips_are_left_out(world):
    ctx, s, t, _ = world
    now = ctx.clock.now()
    add_slip(ctx, t["pacing"], at=now - 15 * DAY)
    add_slip(ctx, t["pacing"], flagged=1)
    add_slip(ctx, t["pacing"], key=False)
    for _i in range(3):
        add_slip(ctx, t["pacing"])
    assert len(slips.review_qids(ctx, s, 5)) == 3


def test_review_asks_only_his_questions_word_for_word_and_grades_in_code(world):
    ctx, s, t, _ = world
    ids = [add_slip(ctx, t["pacing"], f"Mine number {i}?", word=f"w{i}") for i in range(4)]
    run(drill.start(ctx, s, mine=True))
    r = ctx.con.execute("select * from teach_drill where status='live'").fetchone()
    assert r["kind"] == "review" and r["topic_id"] == "" and len(json.loads(r["qids"])) == 4
    asked = []
    for _i in range(4):
        row = ctx.con.execute("select * from teach_drill where status='live'").fetchone()
        q = ctx.con.execute("select * from teach_questions where id=?", (json.loads(row["qids"])[row["i"]],)).fetchone()
        asked.append(q["q"])
        res = run(drill.on_message(ctx, json.loads(q["key_json"])["accept"][0]))
    assert sorted(asked) == [f"Mine number {i}?" for i in range(4)]
    assert res["drill"]["right"] == 4 and res["drill"]["topic"] == "your mistakes"
    assert {r["kind"] for r in ctx.con.execute("select kind from teach_evidence where origin='drill'")} == {"right_alone"}
    assert all(x["rights"] == 1 and x["reviewed_at"] is not None for x in ctx.con.execute("select * from teach_slips where id in (%s)" % ",".join("?" * 4), ids))


def test_review_never_contains_a_question_that_is_not_a_slip_property(world):
    ctx, s, t, _ = world
    rnd = random.Random(7)
    for lv in (1, 2, 3):
        for _i in range(3):
            add_q(ctx, t["pacing"], f"b{next(_ids)}", lv)
    for round_ in range(20):
        ctx.con.execute("DELETE FROM teach_slips"); ctx.con.execute("DELETE FROM teach_drill")
        texts = {f"Slip {round_}-{i}?" for i in range(rnd.randint(3, 9))}
        for q in texts:
            add_slip(ctx, rnd.choice(list(t.values())), q, at=ctx.clock.now() - rnd.randint(0, 13) * DAY)
        qids = slips.review_qids(ctx, None, 5)
        got = {ctx.con.execute("select q from teach_questions where id=?", (x,)).fetchone()[0] for x in qids}
        assert got <= texts and len(qids) == min(5, len(texts))


def test_review_questions_made_for_it_are_never_picked_by_a_normal_drill_or_counted_in_the_bank(world):
    ctx, s, t, _ = world
    for _i in range(3):
        add_slip(ctx, t["pacing"])
    assert len(slips.review_qids(ctx, s, 5)) == 3
    assert bank.usable(ctx, t["pacing"]) == 0
    assert ctx.con.execute("select count(*) from teach_questions where source='slip' and status='review'").fetchone()[0] == 3
    assert slips.review_qids(ctx, s, 5) == slips.review_qids(ctx, s, 5)            # asking again makes no duplicates
    assert ctx.con.execute("select count(*) from teach_questions where source='slip'").fetchone()[0] == 3


def test_right_twice_retires_a_question_wrong_resets(world):
    ctx, s, t, _ = world
    sids = [add_slip(ctx, t["pacing"], f"Q{i}?", word=f"w{i}") for i in range(3)]
    for _round in range(2):
        run(drill.start(ctx, s, mine=True))
        for _i in range(3):
            row = ctx.con.execute("select * from teach_drill where status='live'").fetchone()
            q = ctx.con.execute("select * from teach_questions where id=?", (json.loads(row["qids"])[row["i"]],)).fetchone()
            run(drill.on_message(ctx, json.loads(q["key_json"])["accept"][0] if q["q"] != "Q0?" or _round == 0 else "wrong"))
    rights = {r["question"]: r["rights"] for r in ctx.con.execute("select * from teach_slips where id in ('%s')" % "','".join(sids))}
    assert rights["Q1?"] == 2 and rights["Q2?"] == 2 and rights["Q0?"] == 0
    left = [x["question"] for x in slips.review_candidates(ctx, s, ctx.clock.now())]
    assert "Q1?" not in left and "Q2?" not in left


def test_a_slip_never_reviewed_comes_first(world):
    ctx, s, t, _ = world
    now = ctx.clock.now()
    add_slip(ctx, t["pacing"], "seen before?", reviewed_at=now - DAY, at=now - 100)
    add_slip(ctx, t["pacing"], "fresh one?", at=now - 5 * DAY)
    assert slips.review_candidates(ctx, s, now)[0]["question"] == "fresh one?"


def test_review_gives_the_slip_its_old_bank_row_when_the_words_match(world):
    ctx, s, t, _ = world
    qid = add_q(ctx, t["pacing"], "wx")
    q = ctx.con.execute("select q from teach_questions where id=?", (qid,)).fetchone()[0]
    sid = add_slip(ctx, t["pacing"], q, word="wx")
    add_slip(ctx, t["pacing"]); add_slip(ctx, t["pacing"])
    assert qid in slips.review_qids(ctx, s, 5)
    assert ctx.con.execute("select qid from teach_slips where id=?", (sid,)).fetchone()[0] == qid


# ---- flag -----------------------------------------------------------------------------------------------------------
def test_flag_cancels_the_evidence_and_the_score_matches_a_run_without_it(world):
    ctx, s, t, _ = world
    tm.record(ctx, t["pacing"], "right_alone", "drill", "a")
    base = tm.score(ctx, t["pacing"], ctx.clock.now())
    ev = tm.record(ctx, t["pacing"], "wrong", "drill", "b", "drill question 1")
    assert tm.score(ctx, t["pacing"], ctx.clock.now()) < base
    add_slip(ctx, t["pacing"], "Was it wrong?", evid=ev)
    r = slips.tool(ctx, {"action": "flag"})
    assert r["ok"] and r["flagged"] and slips.flagged_count(ctx) == 1
    assert tm.score(ctx, t["pacing"], ctx.clock.now()) == base
    assert tm.level(ctx, t["pacing"], ctx.clock.now()) == tm.level(ctx, t["pacing"], ctx.clock.now())
    assert ctx.con.execute("select note from teach_evidence where id=?", (ev,)).fetchone()[0].startswith("cancelled")


def test_flag_marks_the_bank_question_bad_and_the_plan_item_and_never_edits_the_key(world):
    ctx, s, t, src = world
    qid = add_q(ctx, t["pacing"], "right1")
    q = ctx.con.execute("select q from teach_questions where id=?", (qid,)).fetchone()[0]
    key_before = ctx.con.execute("select key_json from teach_questions where id=?", (qid,)).fetchone()[0]
    pl = {"steps": [{"idea": "i", "check": {"q": q, "key": {"accept": ["right1"]}}, "explain": []}], "guided": {"q": "g", "key": {}}, "solo": {"q": "s", "key": {}}}
    ctx.con.execute("INSERT INTO teach_plans(id, topic_id, version, plan_json, status, made_at) VALUES ('tpf',?,1,?,'usable',?)", (t["pacing"], json.dumps(pl), ctx.clock.now()))
    add_slip(ctx, t["pacing"], q, qid=qid, word="right1")
    slips.tool(ctx, {"action": "flag"})
    row = ctx.con.execute("select status, key_json from teach_questions where id=?", (qid,)).fetchone()
    assert row["status"] == "bad" and row["key_json"] == key_before
    assert json.loads(ctx.con.execute("select plan_json from teach_plans where id='tpf'").fetchone()[0])["steps"][0]["check"]["bad"] is True
    assert bank.pick(ctx, t["pacing"], 1, 5) == []                              # a bad question is never picked
    assert slips.review_qids(ctx, s, 5) == []


def test_flag_by_words_and_nothing_to_flag(world):
    ctx, s, t, _ = world
    assert not slips.tool(ctx, {"action": "flag"})["ok"]
    add_slip(ctx, t["pacing"], "What is a negative split?", at=ctx.clock.now() - 100)
    add_slip(ctx, t["fuel"], "How many gels in a marathon?", at=ctx.clock.now() - 10)
    assert not slips.tool(ctx, {"action": "flag", "what": "the quantum physics one"})["ok"]
    r = slips.tool(ctx, {"action": "flag", "what": "the negative split question"})
    assert r["ok"] and r["question"] == "What is a negative split?"
    assert slips.flagged_count(ctx) == 1
    assert slips.tool(ctx, {"action": "flag"})["question"] == "How many gels in a marathon?"      # the next default is the other one
    assert not slips.tool(ctx, {"action": "flag"})["ok"]


def test_flagged_slips_leave_the_book_and_the_patterns(world):
    ctx, s, t, _ = world
    add_slip(ctx, t["pacing"], flagged=1)
    assert slips.tool(ctx, {"action": "slips"})["total"] == 0


# ---- patterns -------------------------------------------------------------------------------------------------------
def test_no_pattern_under_eight_slips(world):
    ctx, s, t, _ = world
    for _i in range(7):
        add_slip(ctx, t["pacing"], kind="slipped")
    assert slips.patterns(ctx) == []
    add_slip(ctx, t["pacing"], kind="slipped")
    p = slips.patterns(ctx)
    assert len(p) == 1 and "8 of your last 8" in p[0]["sentence"] and "small slips" in p[0]["sentence"] and "Running" in p[0]["sentence"]


def test_a_kind_under_half_is_no_pattern(world):
    ctx, s, t, _ = world
    for i in range(10):
        add_slip(ctx, t["pacing"], kind=("slipped" if i < 4 else "misread" if i < 8 else "didnt_know"))
    assert slips.patterns(ctx) == []


def test_slips_older_than_28_days_do_not_count(world):
    ctx, s, t, _ = world
    for _i in range(9):
        add_slip(ctx, t["pacing"], kind="slipped", at=ctx.clock.now() - 30 * DAY)
    assert slips.patterns(ctx) == []


def test_timed_pattern_needs_four_timed_and_twice_the_rate(world):
    ctx, s, t, _ = world
    for i in range(4):
        add_slip(ctx, t["pacing"], kind="slipped" if i < 3 else "didnt_know", timed=1)
    for i in range(4):
        add_slip(ctx, t["pacing"], kind="slipped" if i == 0 else "didnt_know", timed=0)
    p = [x for x in slips.patterns(ctx) if x["key"].endswith(":timed")]
    assert len(p) == 1 and "3 of 4 timed ones, against 1 of 4 untimed" in p[0]["sentence"]
    ctx.con.execute("DELETE FROM teach_slips WHERE timed=1 AND id=(SELECT id FROM teach_slips WHERE timed=1 LIMIT 1)")
    add_slip(ctx, t["pacing"], kind="didnt_know", timed=0)                 # now 3 timed: not enough
    assert not [x for x in slips.patterns(ctx) if x["key"].endswith(":timed")]


def test_a_pattern_is_said_once_and_again_after_28_days(world, monkeypatch, clock):
    ctx, s, t, _ = world
    told = []

    async def fake_submit(c, m):
        told.append(m)
        return gate.Decision("say", "test")
    monkeypatch.setattr(gate, "submit", fake_submit)
    for _i in range(9):
        add_slip(ctx, t["pacing"], kind="slipped")
    assert run(slips.say_patterns(ctx)) == 1 and len(told) == 1 and told[0].own is False
    assert run(slips.say_patterns(ctx)) == 0
    clock.advance(10 * DAY)
    for r in ctx.con.execute("select id from teach_slips").fetchall():
        ctx.con.execute("update teach_slips set at=? where id=?", (ctx.clock.now() - 100, r["id"]))
    assert run(slips.say_patterns(ctx)) == 0 and len(told) == 1
    clock.advance(19 * DAY)
    for r in ctx.con.execute("select id from teach_slips").fetchall():
        ctx.con.execute("update teach_slips set at=? where id=?", (ctx.clock.now() - 100, r["id"]))
    assert run(slips.say_patterns(ctx)) == 1 and len(told) == 2


def test_the_label_job_says_patterns_after_labelling(world, monkeypatch, fake_or):
    ctx, s, t, _ = world
    told = []

    async def fake_submit(c, m):
        told.append(m)
        return gate.Decision("say", "test")
    monkeypatch.setattr(gate, "submit", fake_submit)
    for _i in range(8):
        add_slip(ctx, t["pacing"], kind="slipped")
    sid = add_slip(ctx, t["pacing"])
    fake_or.chat_returns("slipped")

    class J:
        id = "j1"
        payload = {"slip_id": sid}
    run(slips.teach_slip_label_job(J(), ctx))
    assert len(told) == 1


# ---- the tool is wired ---------------------------------------------------------------------------------------------------
def test_live_tool_dispatches_slips_and_flag(world):
    ctx, s, t, _ = world
    add_slip(ctx, t["pacing"])
    assert run(live.tool(ctx, {"action": "slips"}))["total"] == 1
    assert run(live.tool(ctx, {"action": "flag"}))["flagged"] is True


def test_chat_schema_has_the_new_actions():
    from misty_engine import chat
    f = next(x for x in chat.TOOLS if x["function"]["name"] == "teach")["function"]["parameters"]["properties"]
    assert {"slips", "flag", "drill"} <= set(f["action"]["enum"]) and "mine" in f and "since" in f
