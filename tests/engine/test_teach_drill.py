"""E11: drills, review and "what am I worst at". Fake model, term keys graded by code. No paid call. A made-up stranger's subject."""
import asyncio
import itertools
import json
import random

import pytest

from misty_engine.teach import drill
from misty_engine.teach import map as tm

DAY = 86400
_ids = itertools.count(1)


async def _noop(*a, **k):
    return None


def run(coro):
    return asyncio.run(coro)


def add_q(ctx, topic, level=1, source="bank", status="usable", word=None):
    n = next(_ids)
    word = word or f"ans{n}"
    qid = f"q{n}"
    ctx.con.execute("INSERT INTO teach_questions(id, topic_id, level, q, key_json, cite_json, source, status, made_at, uses) VALUES (?,?,?,?,?,?,?,?,?,0)",
                    (qid, topic, level, f"Question {n} about {topic[-4:]}?", json.dumps({"accept": [word]}), "{}", source, status, ctx.clock.now()))
    return qid, word


def fill(ctx, topic, per=3, levels=(1, 2, 3)):
    return [add_q(ctx, topic, lv) for lv in levels for _ in range(per)]


@pytest.fixture
def world(fctx):
    s = tm.add_subject(fctx, "running", "Running")["id"]
    t = {k: tm.add_topic(fctx, s, k, k.title(), {"source_id": "x", "chunk_no": 0, "quote": k})["id"] for k in ("pacing", "fuel", "rest")}
    return fctx, s, t


def answer_for(ctx, qid):
    return json.loads(ctx.con.execute("SELECT key_json FROM teach_questions WHERE id=?", (qid,)).fetchone()[0])["accept"][0]


def solid(ctx, t, n=3):
    for i in range(n):
        tm.record(ctx, t, "right_alone", "drill", f"seed{t}{i}")


def shaky(ctx, t):
    tm.record(ctx, t, "wrong", "drill", f"seedw{t}")


def say(ctx, text):
    return run(drill.on_message(ctx, text))


def live_row(ctx):
    return ctx.con.execute("SELECT * FROM teach_drill WHERE status='live'").fetchone()


def evidence(ctx):
    return [r["kind"] for r in ctx.con.execute("SELECT kind FROM teach_evidence WHERE origin='drill' AND origin_id LIKE 'dr_%' ORDER BY id")]


def current_answer(ctx):
    r = live_row(ctx)
    qids = json.loads(r["qids"])
    return answer_for(ctx, qids[r["i"]])


# ---- the table -------------------------------------------------------------------------------------------------------
def test_table_and_one_live_index(fctx):
    cols = [r[1] for r in fctx.con.execute("pragma table_info(teach_drill)")]
    assert {"id", "subject_id", "topic_id", "kind", "status", "qids", "i", "right", "wrong", "extra", "started_at", "updated_at"} <= set(cols)
    fctx.con.execute("INSERT INTO teach_drill(id, subject_id, topic_id, kind, status, qids, started_at, updated_at) VALUES ('a','s','t','drill','live','[]',1,1)")
    with pytest.raises(Exception):
        fctx.con.execute("INSERT INTO teach_drill(id, subject_id, topic_id, kind, status, qids, started_at, updated_at) VALUES ('b','s','t','drill','live','[]',1,1)")


# ---- the pure machine ----------------------------------------------------------------------------------------------
def test_machine_right_answers_finish_in_five():
    s = drill.new_state(5)
    s, acts = drill.step(s, {"e": "start"})
    assert acts == [{"do": "ask", "i": 0}]
    for i in range(5):
        s, acts = drill.step(s, {"e": "answer", "verdict": "right"})
    assert s["status"] == "done" and s["right"] == 5 and {"do": "finished"} in acts


def test_machine_wrong_writes_evidence_and_slip_and_reveals():
    s, _ = drill.step(drill.new_state(3), {"e": "start"})
    s, acts = drill.step(s, {"e": "answer", "verdict": "wrong"})
    assert {"do": "evidence", "kind": "wrong", "i": 0} in acts and {"do": "slip", "i": 0} in acts and {"do": "reveal", "i": 0} in acts
    assert s["i"] == 1 and s["wrong"] == 1


def test_machine_partly_is_evidence_without_slip():
    s, _ = drill.step(drill.new_state(3), {"e": "start"})
    s, acts = drill.step(s, {"e": "answer", "verdict": "partly"})
    assert {"do": "evidence", "kind": "partly", "i": 0} in acts and not any(a["do"] == "slip" for a in acts)


def test_machine_hint_request_is_wrong_with_the_answer():
    for ev in ("show_me", "stuck"):
        s, _ = drill.step(drill.new_state(3), {"e": "start"})
        s, acts = drill.step(s, {"e": ev})
        assert {"do": "evidence", "kind": "wrong", "i": 0} in acts and {"do": "reveal", "i": 0} in acts and s["i"] == 1


def test_machine_side_question_changes_nothing_and_comes_back():
    s, _ = drill.step(drill.new_state(3), {"e": "start"})
    s2, acts = drill.step(s, {"e": "other"})
    assert s2["i"] == s["i"] and s2["right"] == s["right"] and {"do": "side"} in acts


def test_machine_stop_and_done_ignore_later_events():
    s, _ = drill.step(drill.new_state(3), {"e": "start"})
    s, acts = drill.step(s, {"e": "stop"})
    assert s["status"] == "stopped" and {"do": "stopped"} in acts
    s2, acts2 = drill.step(s, {"e": "answer", "verdict": "right"})
    assert s2 == s and acts2 == []


def test_machine_pause_and_resume_keep_place():
    s, _ = drill.step(drill.new_state(4), {"e": "start"})
    s, _ = drill.step(s, {"e": "answer", "verdict": "right"})
    s, _ = drill.step(s, {"e": "pause"})
    assert s["status"] == "paused" and s["i"] == 1
    s, acts = drill.step(s, {"e": "resume"})
    assert s["status"] == "live" and {"do": "ask", "i": 1} in acts


# ---- picking -------------------------------------------------------------------------------------------------------
def test_topics_with_fewer_than_three_bank_questions_are_never_picked(world):
    ctx, s, t = world
    for k in t.values():
        shaky(ctx, k)
        ctx.clock.advance(1)
    tm.record(ctx, t["pacing"], "wrong", "drill", "extra-wrong")    # lowest score: looked at first
    fill(ctx, t["pacing"], per=1, levels=(1, 2))            # only 2 usable
    fill(ctx, t["fuel"], per=1, levels=(1, 2, 3))           # 3 usable
    r = drill.pick_topic(ctx, s)
    assert r["topic_id"] == t["fuel"]
    jobs = ctx.con.execute("SELECT item_id FROM jobs WHERE kind='teach_bank'").fetchall()
    assert t["pacing"] in [j[0] for j in jobs]               # the short ones are queued to be made


def test_plan_questions_do_not_count_toward_the_three(world):
    ctx, s, t = world
    shaky(ctx, t["pacing"])
    for _ in range(4):
        add_q(ctx, t["pacing"], 1, source="plan")
    r = drill.pick_topic(ctx, s)
    assert r["topic_id"] is None and "make questions" in r["error"]


def test_nothing_has_a_bank_says_so(world):
    ctx, s, t = world
    shaky(ctx, t["pacing"])
    r = drill.pick_topic(ctx, s)
    assert r["topic_id"] is None and "back in a minute" in r["error"]


def test_a_due_topic_comes_before_a_not_due_one(world):
    ctx, s, t = world
    solid(ctx, t["pacing"])                                  # right now: next review is days away
    ctx.clock.advance(DAY * 3)
    solid(ctx, t["fuel"])                                    # fresh, so not due
    for k in t.values():
        fill(ctx, k)
    r = drill.pick_topic(ctx, s)
    assert r["topic_id"] == t["pacing"]


def test_picker_property_random_maps(world):
    ctx, s, t = world
    rnd = random.Random(7)
    for k in t.values():
        fill(ctx, k)
    for trial in range(30):
        ctx.con.execute("DELETE FROM teach_evidence")
        ctx.con.execute("DELETE FROM jobs")
        for k in t.values():
            ctx.con.execute("UPDATE teach_questions SET status='usable' WHERE topic_id=?", (k,))
            if rnd.random() < 0.3:
                ctx.con.execute("UPDATE teach_questions SET status='bad' WHERE topic_id=? AND level>=2", (k,))
            for _ in range(rnd.choice([0, 1, 3])):
                tm.record(ctx, k, rnd.choice(["right_alone", "wrong", "partly"]), "drill", f"e{trial}{k}{rnd.random()}")
            ctx.clock.advance(rnd.choice([0, DAY, 5 * DAY]))
        r = drill.pick_topic(ctx, s)
        now = ctx.clock.now()
        if r["topic_id"] is None:
            continue
        assert drill._bank_usable(ctx, r["topic_id"]) >= 3
        dues = {k: tm.due(ctx, k, now) for k in t.values() if drill._bank_usable(ctx, k) >= 3 and tm.level(ctx, k, now) != "unknown"}
        due_now = [k for k, d in dues.items() if d is not None and d <= now]
        if due_now:
            assert r["topic_id"] in due_now


def test_unknown_topics_are_not_drilled(world):
    ctx, s, t = world
    for k in t.values():
        fill(ctx, k)
    r = drill.pick_topic(ctx, s)
    assert r["topic_id"] is None


def test_levels_follow_the_topic_level(world):
    ctx, s, t = world
    for k in t.values():
        fill(ctx, k)
    assert drill.levels_for("unknown") == [1] and drill.levels_for("seen") == [1]
    assert drill.levels_for("shaky") == [1, 2] and drill.levels_for("solid") == [2, 3] and drill.levels_for("mastered") == [3]


def test_a_shaky_topic_reaches_back_to_its_needs(world):
    ctx, s, t = world
    tm.add_need(ctx, t["pacing"], t["fuel"], "him")
    for k in (t["pacing"], t["fuel"]):
        fill(ctx, k)
    shaky(ctx, t["pacing"])
    out = run(drill.start(ctx, s))
    qids = json.loads(live_row(ctx)["qids"])
    owners = {ctx.con.execute("SELECT topic_id FROM teach_questions WHERE id=?", (q,)).fetchone()[0] for q in qids}
    assert out["ok"] and len(qids) == 5 and t["pacing"] in owners and t["fuel"] in owners


def test_solid_topic_does_not_reach_back(world):
    ctx, s, t = world
    tm.add_need(ctx, t["pacing"], t["fuel"], "him")
    for k in (t["pacing"], t["fuel"]):
        fill(ctx, k)
    solid(ctx, t["pacing"])
    ctx.clock.advance(DAY * 2)
    run(drill.start(ctx, s))
    owners = {ctx.con.execute("SELECT topic_id FROM teach_questions WHERE id=?", (q,)).fetchone()[0] for q in json.loads(live_row(ctx)["qids"])}
    assert owners == {t["pacing"]}


# ---- running a drill -----------------------------------------------------------------------------------------------
def start_one(world, quick=False):
    ctx, s, t = world
    for k in t.values():
        fill(ctx, k)
    shaky(ctx, t["pacing"])
    return run(drill.start(ctx, s, quick=quick))


def test_start_asks_the_first_question_and_is_five_long(world):
    out = start_one(world)
    ctx = world[0]
    assert out["ok"] and out["do"][0]["ask"].startswith("Question") and len(json.loads(live_row(ctx)["qids"])) == 5
    assert out["drill"]["of"] == 5


def test_quick_is_three(world):
    start_one(world, quick=True)
    assert len(json.loads(live_row(world[0])["qids"])) == 3


def test_a_perfect_drill_writes_five_right_alone(world):
    start_one(world)
    ctx = world[0]
    for _ in range(5):
        say(ctx, current_answer(ctx))
    assert evidence(ctx) == ["right_alone"] * 5
    assert tuple(ctx.con.execute("SELECT status, right FROM teach_drill").fetchone()) == ("done", 5)


def test_wrong_answer_writes_wrong_and_a_slip_with_the_right_answer(world):
    start_one(world)
    ctx = world[0]
    out = say(ctx, "no idea what that is called")      # not a hint request, just wrong
    assert evidence(ctx) == ["wrong"]
    slip = ctx.con.execute("SELECT * FROM teach_slips").fetchone()
    assert slip["question"].startswith("Question") and slip["right_answer"].startswith("ans") and slip["evidence_id"]
    assert any("reveal" in d for d in out["do"])


def test_evidence_is_never_written_twice_for_one_answer(world):
    start_one(world)
    ctx = world[0]
    r = live_row(ctx)
    say(ctx, current_answer(ctx))
    n = len(evidence(ctx))
    drill._evidence(ctx, ctx.con.execute("SELECT * FROM teach_drill").fetchone(), "right_alone", 0)
    assert len(evidence(ctx)) == n


def test_hint_request_shows_the_answer_and_counts_wrong(world):
    start_one(world)
    ctx = world[0]
    out = say(ctx, "give me a hint")
    assert evidence(ctx) == ["wrong"] and any("reveal" in d for d in out["do"])


def test_side_question_is_answered_and_the_question_comes_back(world):
    start_one(world)
    ctx = world[0]
    before = dict(live_row(ctx))
    out = say(ctx, "what is a negative split anyway?")
    assert out["back_to"]["question"].startswith("Question") and evidence(ctx) == []
    assert live_row(ctx)["i"] == before["i"]


def test_stop_ends_it_and_keeps_the_evidence_so_far(world):
    start_one(world)
    ctx = world[0]
    say(ctx, current_answer(ctx))
    out = say(ctx, "stop")
    assert out["stopped"] and live_row(ctx) is None and evidence(ctx) == ["right_alone"]
    assert ctx.con.execute("SELECT status FROM teach_drill").fetchone()[0] == "stopped"


def test_restart_in_the_middle_changes_nothing(world):
    start_one(world)
    ctx = world[0]
    say(ctx, current_answer(ctx))
    say(ctx, current_answer(ctx))
    # the row is the whole state: a new reader sees the same place
    r = live_row(ctx)
    assert r["i"] == 2 and r["right"] == 2
    out = say(ctx, current_answer(ctx))
    assert live_row(ctx)["i"] == 3 and evidence(ctx) == ["right_alone"] * 3


def test_thirty_minute_gap_pauses_and_carry_on_resumes_in_place(world):
    start_one(world)
    ctx = world[0]
    say(ctx, current_answer(ctx))
    ctx.clock.advance(31 * 60)
    assert drill.current(ctx) is None and drill.paused(ctx) is not None
    out = say(ctx, "carry on")
    assert out["ok"] and live_row(ctx)["i"] == 1 and out["do"][0]["ask"].startswith("Question")


def test_a_message_is_not_for_a_paused_drill_unless_carry_on(world):
    start_one(world)
    ctx = world[0]
    ctx.clock.advance(31 * 60)
    assert say(ctx, "what's the weather") is None


def test_only_one_live_thing_at_a_time(world):
    start_one(world)
    ctx, s, t = world
    again = run(drill.start(ctx, s))
    assert again["ok"] is False and "finish or stop" in again["error"]


def test_a_live_lesson_blocks_a_drill(world):
    ctx, s, t = world
    for k in t.values():
        fill(ctx, k)
    shaky(ctx, t["pacing"])
    ctx.con.execute("INSERT INTO teach_plans(id, topic_id, version, plan_json, model, cost, status, why, made_at) VALUES ('p',?,1,'{}','h',0,'usable','',1)", (t["pacing"],))
    ctx.con.execute("INSERT INTO teach_live(id, topic_id, plan_id, status, started_at, updated_at) VALUES ('lv',?,'p','live',?,?)", (t["pacing"], ctx.clock.now(), ctx.clock.now()))
    out = run(drill.start(ctx, s))
    assert out["ok"] is False and "lesson" in out["error"]


def test_questions_are_marked_used(world):
    start_one(world)
    ctx = world[0]
    qid = json.loads(live_row(ctx)["qids"])[0]
    assert ctx.con.execute("SELECT uses FROM teach_questions WHERE id=?", (qid,)).fetchone()[0] == 1


# ---- 20 scripted drills ----------------------------------------------------------------------------------------------
SCRIPTS = [
    ("right " * 5, ["right_alone"] * 5, "done"),
    ("wrong " * 5, ["wrong"] * 5, "done"),
    ("right wrong right wrong right", ["right_alone", "wrong", "right_alone", "wrong", "right_alone"], "done"),
    ("right right stop", ["right_alone"] * 2, "stopped"),
    ("stop", [], "stopped"),
    ("side right right right right right", ["right_alone"] * 5, "done"),
    ("right side side right right right", ["right_alone"] * 4, "live"),
    ("hint right right right right", ["wrong"] + ["right_alone"] * 4, "done"),
    ("show right right right right", ["wrong"] + ["right_alone"] * 4, "done"),
    ("wrong stop", ["wrong"], "stopped"),
    ("right right right right right", ["right_alone"] * 5, "done"),
    ("right gap right right right right", ["right_alone"] * 5, "done"),
    ("right right gap gap right right right", ["right_alone"] * 5, "done"),
    ("wrong gap right right right right", ["wrong"] + ["right_alone"] * 4, "done"),
    ("hint hint hint hint hint", ["wrong"] * 5, "done"),
    ("right wrong side hint right", ["right_alone", "wrong", "wrong", "right_alone"], "live"),
    ("side stop", [], "stopped"),
    ("right right right right wrong", ["right_alone"] * 4 + ["wrong"], "done"),
    ("gap stop", [], "stopped"),
    ("right gap wrong right right right", ["right_alone", "wrong", "right_alone", "right_alone", "right_alone"], "done"),
]


@pytest.mark.parametrize("script,want_kinds,want_status", SCRIPTS)
def test_scripted_drills_end_as_expected(world, script, want_kinds, want_status):
    start_one(world)
    ctx = world[0]
    for w in script.split():
        if w == "gap":
            ctx.clock.advance(31 * 60)
            assert say(ctx, "carry on")["ok"]
            continue
        text = {"right": lambda: current_answer(ctx), "wrong": lambda: "banana", "side": lambda: "what does that word mean?",
                "hint": lambda: "give me a hint", "show": lambda: "show me", "stop": lambda: "stop"}[w]()
        say(ctx, text)
    assert evidence(ctx) == want_kinds
    st = ctx.con.execute("SELECT status FROM teach_drill").fetchone()[0]
    assert st == want_status


# ---- the tool: drill and level -----------------------------------------------------------------------------------
def test_tool_drill_starts_one(world):
    ctx, s, t = world
    for k in t.values():
        fill(ctx, k)
    shaky(ctx, t["fuel"])
    out = run(drill.tool(ctx, {"action": "drill", "subject": "running"}))
    assert out["ok"] and out["drill"]["topic"] == "Fuel"


def test_tool_drill_quick(world):
    ctx, s, t = world
    for k in t.values():
        fill(ctx, k)
    shaky(ctx, t["fuel"])
    out = run(drill.tool(ctx, {"action": "drill", "quick": True}))
    assert out["drill"]["of"] == 3


def test_level_lists_the_weakest_three_with_reasons(world):
    ctx, s, t = world
    shaky(ctx, t["pacing"])
    tm.record(ctx, t["fuel"], "class_lost", "class", "c1:lost:abc")
    tm.record(ctx, t["fuel"], "right_alone", "drill", "z")
    solid(ctx, t["rest"])
    out = drill.level(ctx, {"subject": "running"})
    assert out["ok"] and out["weakest"][0]["topic"] == "Pacing" and "wrong" in out["weakest"][0]["why"]
    assert len(out["weakest"]) <= 3
    assert not ctx.con.execute("SELECT 1 FROM teach_drill").fetchone()         # read-only: never opens a drill


def test_level_for_one_topic(world):
    ctx, s, t = world
    shaky(ctx, t["pacing"])
    out = drill.level(ctx, {"topic": "pacing"})
    assert out["ok"] and out["topic"]["level"] == "shaky"


def test_level_with_nothing_shaky_says_so(world):
    ctx, s, t = world
    out = drill.level(ctx, {})
    assert out["ok"] and out["weakest"] == []


def test_level_lists_due_ones(world):
    ctx, s, t = world
    solid(ctx, t["rest"])
    ctx.clock.advance(DAY * 30)
    out = drill.level(ctx, {})
    assert "Rest" in out["due"]


# ---- chat hooks ----------------------------------------------------------------------------------------------------
def test_pending_says_drill_when_one_is_live(world):
    from misty_engine import chat
    start_one(world)
    assert chat.teach_pending(world[0]) == "drill"


def test_text_she_read_cannot_start_a_drill(world):
    from misty_engine import chat
    ctx = world[0]
    out = run(chat.run_tool(ctx, "teach", {"action": "drill"}, on_status=_noop, confirm=None, source="text"))
    assert out["ok"] is False


def test_teach_tool_enum_has_drill_and_level():
    from misty_engine import chat
    t = next(t for t in chat.TOOLS if t["function"]["name"] == "teach")
    assert {"drill", "level"} <= set(t["function"]["parameters"]["properties"]["action"]["enum"])
    assert "quick" in t["function"]["parameters"]["properties"]


def test_rule_block_names_the_drill_only_while_one_is_live(world):
    from misty_engine.teach import live
    ctx = world[0]
    assert "drill is running" not in live.rule_block(ctx)
    start_one(world)
    assert "drill is running" in live.rule_block(ctx)
