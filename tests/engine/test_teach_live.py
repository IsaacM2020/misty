"""E7: the live lesson (row, grading, evidence, slips, pause, tool) with a fake model and term keys (graded by code). No paid call."""
import asyncio
import json

import pytest

from misty_engine.teach import ask, lesson, live, plan, sources
from misty_engine.teach import map as tm

PASSAGE = "A negative split is when the second half of a run is faster than the first half of the run."


def run(coro):
    return asyncio.run(coro)


def cite(src):
    return {"source_id": src, "chunk_no": 0, "quote": PASSAGE[:80]}


def make_plan(src, n=3, prefix=""):
    def item(q, word, hints=True):
        return {"q": q, "key": {"accept": [word]}, "hints": ["hint one", "hint two", "hint three"] if hints else [], "cite": cite(src)}
    steps = []
    for i in range(n):
        steps.append({"idea": f"idea {i}", "check": item(f"Question {i}?", f"ans{i}"),
                      "explain": [{"way": w, "say": f"say {w} {i}", "board": None, "cite": cite(src)} for w in plan.WAYS]})
    return {"steps": steps, "guided": item("Guided question?", "gans"), "solo": item("Solo question?", "sans"), "recap": "the recap words"}


def put_plan(ctx, topic_id, src, n=3, usable=True):
    pid = "tp_" + topic_id[-6:]
    ctx.con.execute("INSERT OR REPLACE INTO teach_plans(id, topic_id, version, plan_json, model, cost, status, why, made_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (pid, topic_id, 1, json.dumps(make_plan(src, n)), "haiku", 0.0, "usable" if usable else "rejected", "", ctx.clock.now()))
    return pid


@pytest.fixture
def src(fctx):
    sid = sources._keep(fctx, "page", "https://x.test/run", "run", "x")["id"]
    fctx.con.execute("DELETE FROM teach_chunks WHERE source_id=?", (sid,))
    fctx.con.execute("INSERT INTO teach_chunks(source_id, chunk_no, text_scrubbed) VALUES (?,0,?)", (sid, PASSAGE))
    return sid


@pytest.fixture
def topic(fctx, src):
    s = tm.add_subject(fctx, "running", "Running")["id"]
    t = tm.add_topic(fctx, s, "negative split", "Negative splits", {"source_id": src, "chunk_no": 0, "quote": PASSAGE[:60]})["id"]
    put_plan(fctx, t, src)
    return t


def kinds(ctx):
    return [r["kind"] for r in ctx.con.execute("select kind from teach_evidence where origin='lesson' order by id")]


def say(ctx, text):
    return run(live.on_message(ctx, text))


def right_for(ctx):
    r = live.current(ctx)
    s = live._state(r)
    it = live._item(live._plan(ctx, r), s["phase"], s["step"])
    return it["key"]["accept"][0]


# ---- starting ------------------------------------------------------------------------------------------------------
def test_start_makes_a_live_row_and_says_step_zero_plain_then_the_question(fctx, topic):
    r = run(live.start(fctx, topic))
    assert r["ok"] and r["first"]
    assert r["do"][0]["way"] == "plain" and r["do"][0]["explain"] == "say plain 0" and r["do"][1]["ask"] == "Question 0?"
    row = live.current(fctx)
    assert row["status"] == "live" and row["step_no"] == 0 and live.pending(fctx) == "lesson"
    assert "ans0" not in json.dumps(r["do"])                                            # the model never sees the answer before a reveal


def test_one_live_row_at_most(fctx, topic, src):
    run(live.start(fctx, topic))
    run(live.start(fctx, topic))
    assert fctx.con.execute("select count(*) from teach_live where status='live'").fetchone()[0] == 1
    assert fctx.con.execute("select count(*) from teach_live where status='stopped'").fetchone()[0] == 1


def test_no_plan_makes_one_first(fctx, src, monkeypatch):
    s = tm.add_subject(fctx, "running", "Running")["id"]
    t = tm.add_topic(fctx, s, "fuel", "Fuelling", {"source_id": src, "chunk_no": 0, "quote": PASSAGE[:60]})["id"]
    made = []

    async def fake(ctx, t_, **kw):
        made.append(t_)
        put_plan(ctx, t_, src)
        return {"status": "usable", "why": ""}
    monkeypatch.setattr(plan, "make", fake)
    r = run(live.start(fctx, t))
    assert made == [t] and r["ok"] and r["plan_made_now"]


def test_a_plan_that_cannot_be_made_says_why(fctx, src, monkeypatch):
    s = tm.add_subject(fctx, "running", "Running")["id"]
    t = tm.add_topic(fctx, s, "fuel", "Fuelling", {"source_id": src, "chunk_no": 0, "quote": PASSAGE[:60]})["id"]

    async def fake(ctx, t_, **kw):
        return {"status": "rejected", "why": "only 1 of 4 checks passed"}
    monkeypatch.setattr(plan, "make", fake)
    r = run(live.start(fctx, t))
    assert not r["ok"] and "only 1 of 4" in r["error"]


# ---- his answers are graded by code --------------------------------------------------------------------------------
def test_right_answer_writes_evidence_and_moves_on(fctx, topic):
    run(live.start(fctx, topic))
    r = say(fctx, "I think it is ans0")
    assert r["do"][0]["verdict"] == "right" and kinds(fctx) == ["right_helped"] and live.current(fctx)["step_no"] == 1
    assert r["do"][1]["explain"] == "say plain 1"


def test_wrong_answer_uses_the_next_way_and_writes_a_slip(fctx, topic):
    run(live.start(fctx, topic))
    r = say(fctx, "banana")
    assert r["do"][0]["verdict"] == "wrong" and r["do"][1]["way"] == "picture" and kinds(fctx) == ["wrong"]
    sl = fctx.con.execute("select * from teach_slips").fetchall()
    assert len(sl) == 1 and sl[0]["right_answer"] == "ans0" and sl[0]["his_answer"] == "banana" and sl[0]["question"] == "Question 0?"
    r = say(fctx, "banana")
    assert r["do"][1]["way"] == "example" and kinds(fctx) == ["wrong"]                  # one wrong per step
    assert fctx.con.execute("select count(*) from teach_slips").fetchone()[0] == 2


def test_third_miss_shows_the_answer_and_it_is_the_only_time_the_key_is_said(fctx, topic):
    run(live.start(fctx, topic))
    say(fctx, "banana")
    say(fctx, "banana")
    r = say(fctx, "banana")
    rev = [d for d in r["do"] if "reveal" in d]
    assert rev and rev[0]["reveal"] == "ans0" and live.current(fctx)["step_no"] == 1


def test_stuck_hints_and_show_me(fctx, topic):
    run(live.start(fctx, topic))
    r = say(fctx, "I'm stuck")
    assert r["do"][0] == {"hint": "hint one"} and live.current(fctx)["hints_used"] == 1
    r = say(fctx, "give me a hint")
    assert r["do"][0] == {"hint": "hint two"}
    r = say(fctx, "show me the answer")
    assert r["do"][0]["reveal"] == "ans0" and live.current(fctx)["step_no"] == 1 and kinds(fctx) == []


def test_a_side_question_changes_nothing_and_comes_back(fctx, topic):
    run(live.start(fctx, topic))
    before = dict(live.current(fctx))
    r = say(fctx, "why do runners do that?")
    assert r["do"][0] == {"side_question": True} and r["do"][-1] == {"then_ask": "Question 0?"}
    after = live.current(fctx)
    for k in ("step_no", "phase", "way_no", "misses", "hints_used"):
        assert before[k] == after[k]
    assert kinds(fctx) == []
    r = say(fctx, "thanks")                                                             # not an answer at all: also a side message
    assert r["do"][0] == {"side_question": True}


def test_stop_says_stopped_and_ends_the_lesson(fctx, topic):
    run(live.start(fctx, topic))
    r = say(fctx, "stop")
    assert {"stopped": True} in r["do"] and live.current(fctx) is None and live.pending(fctx) is None


def test_no_lesson_means_the_hook_does_nothing(fctx, topic):
    assert say(fctx, "ans0") is None and say(fctx, "carry on") is None


# ---- a full lesson ------------------------------------------------------------------------------------------------
def test_a_whole_lesson_ends_done_with_taught_and_recap(fctx, topic):
    run(live.start(fctx, topic))
    for _ in range(3):
        say(fctx, right_for(fctx))
    r = say(fctx, "gans")
    assert r["lesson"]["phase"] == "solo" and r["do"][-1]["note"].startswith("Now one on his own")
    r = say(fctx, "sans")
    assert {"recap": "the recap words"} in r["do"] and {"finished": True} in r["do"]
    assert kinds(fctx) == ["right_helped"] * 4 + ["right_alone", "taught"]
    assert live.current(fctx) is None and fctx.con.execute("select status from teach_live").fetchone()[0] == "done"


# ---- pause, resume, restart -----------------------------------------------------------------------------------------
def test_left_alone_for_thirty_minutes_pauses_and_carry_on_resumes_in_place(fctx, topic, clock):
    run(live.start(fctx, topic))
    say(fctx, "ans0")
    say(fctx, "banana")                                                                  # step 1, way 1
    clock.advance(31 * 60)
    assert live.current(fctx) is None and fctx.con.execute("select status from teach_live").fetchone()[0] == "paused"
    assert say(fctx, "how is the weather") is None                                       # paused: normal chat
    r = say(fctx, "carry on")
    row = live.current(fctx)
    assert r["ok"] and row["step_no"] == 1 and row["way_no"] == 1 and row["status"] == "live"
    assert r["do"][-1]["ask"] == "Question 1?"


def test_resume_by_tool_and_too_old_pauses_are_forgotten(fctx, topic, clock):
    run(live.start(fctx, topic))
    clock.advance(40 * 60)
    live.current(fctx)
    r = run(live.tool(fctx, {"action": "resume"}))
    assert r["ok"] and live.current(fctx) is not None
    clock.advance(40 * 60)
    live.current(fctx)
    clock.advance(13 * 3600)
    assert run(live.tool(fctx, {"action": "resume"}))["ok"] is False and say(fctx, "carry on") is None


def test_the_row_is_the_state_so_a_restart_changes_nothing(fctx, topic):
    run(live.start(fctx, topic))
    say(fctx, "ans0")
    say(fctx, "banana")
    rid = live.current(fctx)["id"]
    s = live._state(live.current(fctx))
    # a restart is a new process reading the same row: nothing is held anywhere else
    again = live._state(live._row(fctx, rid))
    assert again == s and again["step"] == 1 and again["way"] == 1 and again["wrong"] is True
    r = say(fctx, "ans1")
    assert r["do"][0]["verdict"] == "right" and kinds(fctx) == ["right_helped", "wrong", "right_helped"]


# ---- the need offer --------------------------------------------------------------------------------------------------
def test_third_miss_with_a_weak_need_offers_it_and_yes_starts_that_lesson(fctx, topic, src):
    s = fctx.con.execute("select subject_id from teach_topics where id=?", (topic,)).fetchone()[0]
    need = tm.add_topic(fctx, s, "pacing", "Pacing", {"source_id": src, "chunk_no": 0, "quote": PASSAGE[:60]})["id"]
    put_plan(fctx, need, src)
    tm.add_need(fctx, topic, need, "him")
    run(live.start(fctx, topic))
    say(fctx, "banana")
    say(fctx, "banana")
    r = say(fctx, "banana")
    assert r["do"][-1] == {"offer": "Pacing"} and live.current(fctx) is None
    assert fctx.con.execute("select status from teach_live").fetchone()[0] == "paused"
    out = say(fctx, "yes")
    row = live.current(fctx)
    assert out["ok"] and row["topic_id"] == need and out["lesson"]["topic"] == "Pacing"


def test_offer_no_shows_the_answer_and_moves_on(fctx, topic, src):
    s = fctx.con.execute("select subject_id from teach_topics where id=?", (topic,)).fetchone()[0]
    need = tm.add_topic(fctx, s, "pacing", "Pacing", {"source_id": src, "chunk_no": 0, "quote": PASSAGE[:60]})["id"]
    tm.add_need(fctx, topic, need, "him")
    run(live.start(fctx, topic))
    for _ in range(3):
        say(fctx, "banana")
    r = say(fctx, "no")
    assert any("reveal" in d for d in r["do"]) and live.current(fctx)["step_no"] == 1


def test_a_solid_need_does_not_stop_the_lesson(fctx, topic, src):
    s = fctx.con.execute("select subject_id from teach_topics where id=?", (topic,)).fetchone()[0]
    need = tm.add_topic(fctx, s, "pacing", "Pacing", {"source_id": src, "chunk_no": 0, "quote": PASSAGE[:60]})["id"]
    tm.add_need(fctx, topic, need, "him")
    tm.record(fctx, need, "right_alone", "chat")
    run(live.start(fctx, topic))
    for _ in range(3):
        r = say(fctx, "banana")
    assert any("reveal" in d for d in r["do"]) and live.current(fctx)["step_no"] == 1


# ---- the tool ------------------------------------------------------------------------------------------------------
def test_tool_start_finds_a_topic_by_words_or_picks_a_ready_one(fctx, topic):
    r = run(live.tool(fctx, {"action": "start", "topic": "negative split"}))
    assert r["ok"] and r["lesson"]["topic"] == "Negative splits"
    run(live.tool(fctx, {"action": "stop"}))
    r = run(live.tool(fctx, {"action": "start", "subject": "running"}))
    assert r["ok"] and r["lesson"]["topic"] == "Negative splits"


def test_tool_errors_are_plain(fctx):
    assert "nothing to learn" in run(live.tool(fctx, {"action": "start"}))["error"]
    assert run(live.tool(fctx, {"action": "stop"}))["error"] == "no lesson is running"
    assert run(live.tool(fctx, {"action": "easier"}))["ok"] is False


def test_tool_easier_again_harder_on_the_live_lesson(fctx, topic):
    run(live.start(fctx, topic))
    r = run(live.tool(fctx, {"action": "easier"}))
    assert r["do"][0]["way"] == "picture" and live.current(fctx)["misses"] == 0
    r = run(live.tool(fctx, {"action": "harder"}))
    assert r["lesson"]["phase"] == "guided"


def test_teach_rule_only_while_a_lesson_is_live(fctx, topic):
    assert live.rule_block(fctx) == ""
    run(live.start(fctx, topic))
    assert "teach tool" in live.rule_block(fctx)


# ---- 20 scripted lessons -------------------------------------------------------------------------------------------
# R right, W wrong, S stuck, Q side question, H show me, G a 31 minute gap then "carry on", X restart (re-read the row), E easier,
# A again, T stop. Plan: 3 checked steps, guided, solo. rh = right_helped, ra = right_alone, w = wrong.
RH, RA, W, TAUGHT = "right_helped", "right_alone", "wrong", "taught"
SCRIPTS = [
    ("RRRRR", [RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("WRRRRR", [W, RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("WWRRRRR", [W, RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("WWWRRRR", [W, RH, RH, RH, RA, TAUGHT], "done"),
    ("SRRRRR", [RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("HRRRR", [RH, RH, RH, RA, TAUGHT], "done"),
    ("QRRRRR", [RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("RGRRRR", [RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("RXRRRR", [RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("RRRRW", [RH, RH, RH, RH, W, TAUGHT], "done"),
    ("RRRHR", [RH, RH, RH, RA, TAUGHT], "done"),
    ("RRRRH", [RH, RH, RH, RH, W, TAUGHT], "done"),
    ("RRRWWWR", [RH, RH, RH, RA, TAUGHT], "done"),
    ("RRRWRR", [RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("RT", [RH], "stopped"),
    ("ERRRRR", [RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("ARRRRR", [RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("WGRRRRR", [W, RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("WQWRRRRR", [W, RH, RH, RH, RH, RA, TAUGHT], "done"),
    ("RRRWSRR", [RH, RH, RH, RH, RA, TAUGHT], "done"),
]
TEXT = {"S": "I'm stuck", "Q": "why does that work?", "H": "show me the answer", "E": "explain it another way", "A": "say that again", "T": "stop", "W": "banana"}


@pytest.mark.parametrize("script,expected,status", SCRIPTS)
def test_scripted_lesson(fctx, topic, clock, script, expected, status):
    run(live.start(fctx, topic))
    for ch in script:
        if ch == "G":
            clock.advance(31 * 60)
            assert say(fctx, "carry on")["ok"]
        elif ch == "X":
            rid = live.current(fctx)["id"]
            assert live._state(live._row(fctx, rid)) == live._state(live.current(fctx))
        elif ch == "R":
            say(fctx, right_for(fctx))
        else:
            say(fctx, TEXT[ch])
    assert kinds(fctx) == expected
    assert fctx.con.execute("select status from teach_live order by started_at desc limit 1").fetchone()[0] == status
    ids = [(r["origin_id"], r["kind"]) for r in fctx.con.execute("select origin_id, kind from teach_evidence")]
    assert len(ids) == len(set(ids))                                                      # never written twice for one answer
    assert fctx.con.execute("select count(*) from teach_slips").fetchone()[0] == script.count("W") + (1 if script == "RRRRH" else 0)   # a show me in solo counts as wrong, so it is in the book too


# ---- E8: a side question always comes back to the same place --------------------------------------------------------
def _to_phase(ctx, phase, step=0):
    """Answer right until the lesson is at `phase` (and, for check, `step`)."""
    for _ in range(20):
        st = live._state(live.current(ctx))
        if st["phase"] == phase and (phase != "check" or st["step"] == step):
            return
        say(ctx, right_for(ctx))
    raise AssertionError("never got there")


@pytest.mark.parametrize("phase,step", [("check", 0), ("check", 2), ("guided", 0), ("solo", 0)])
def test_back_to_names_the_same_phase_and_question(fctx, topic, phase, step):
    run(live.start(fctx, topic))
    _to_phase(fctx, phase, step)
    before = dict(live.current(fctx))
    ev0 = kinds(fctx)
    r = say(fctx, "why does that work?")
    b = r["back_to"]
    st = live._state(live.current(fctx))
    assert (b["phase"], b["step_no"]) == (phase, before["step_no"]) == (st["phase"], st["step"])
    assert b["question"] == r["do"][-1]["then_ask"] and b["question"].endswith("?")
    assert kinds(fctx) == ev0                         # no extra evidence
    for k in ("step_no", "phase", "way_no", "misses", "hints_used"):
        assert before[k] == live.current(fctx)[k]


def test_back_to_after_a_hint_is_the_same_question_and_keeps_the_hint_count(fctx, topic):
    run(live.start(fctx, topic))
    say(fctx, "I'm stuck")
    before = dict(live.current(fctx))
    r = say(fctx, "why do runners do that?")
    assert r["back_to"]["question"] == "Question 0?" and live.current(fctx)["hints_used"] == before["hints_used"] >= 1
    assert kinds(fctx) == []


def test_other_results_have_no_back_to(fctx, topic):
    run(live.start(fctx, topic))
    assert "back_to" not in say(fctx, right_for(fctx))


def test_a_long_check_question_is_cut_for_the_ear_but_keeps_every_word(fctx, topic, src):
    long_q = ("A runner covers the first half of a ten kilometre race in twenty six minutes and the second half in twenty four "
              "minutes after settling into a steady rhythm. What was the pace pattern called?")
    pl = json.loads(fctx.con.execute("select plan_json from teach_plans").fetchone()[0])
    pl["steps"][0]["check"]["q"] = long_q
    fctx.con.execute("update teach_plans set plan_json=?", (json.dumps(pl),))
    r = run(live.start(fctx, topic))
    ask_ = [d["ask"] for d in r["do"] if "ask" in d][0]
    assert "\n\n" in ask_ and ask_.replace("\n\n", " ").split() == long_q.split()
