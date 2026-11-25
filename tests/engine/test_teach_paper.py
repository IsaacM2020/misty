"""E13: paper mode (code says `paper`, never `exam`). A made-up stranger's paper and scheme. Fake model replies only, no paid call."""
import asyncio
import json
import random

import pytest

from misty_engine.teach import build as tb
from misty_engine.teach import drill, keys, live, paper, slips, sources
from misty_engine.teach import map as tm

PAPER = ("1 State what a negative split is. [2]\n"
         "2 Explain why fuel matters in a long run. [3]\n"
         "3 Give one reason to warm up before a race. [1]")
SCHEME = ("1 the second half is faster than the first half (1) the whole run is paced evenly (1)\n"
          "2 carbohydrate stores run out late in a run (1) the legs slow down without fuel (1) eating early keeps the pace up (1)\n"
          "3 warm muscles are less likely to be injured (1)")


def run(coro):
    return asyncio.run(coro)


def q(no, text, marks, quote, points, chunk=0):
    return {"no": no, "text": text, "marks": marks, "cite": {"chunk_no": chunk, "quote": quote},
            "points": [{"text": t, "quote": qu, "chunk_no": 0} for t, qu in points]}


GOOD = [
    q("1", "State what a negative split is.", 2, "State what a negative split is",
      [("second half faster", "second half is faster than the first half"), ("even pacing", "the whole run is paced evenly")]),
    q("2", "Explain why fuel matters in a long run.", 3, "Explain why fuel matters in a long run",
      [("stores run out", "carbohydrate stores run out late in a run"), ("legs slow", "the legs slow down without fuel"),
       ("eat early", "eating early keeps the pace up")]),
    q("3", "Give one reason to warm up before a race.", 1, "Give one reason to warm up before a race",
      [("less injury", "warm muscles are less likely to be injured")]),
]


@pytest.fixture
def world(fctx):
    pid = sources._keep(fctx, "file", "/tmp/9999_qp_1.txt", "9999_qp_1", PAPER)["id"]
    sid = sources._keep(fctx, "file", "/tmp/9999_ms_1.txt", "9999_ms_1", SCHEME)["id"]
    s = tm.add_subject(fctx, "running", "Running")["id"]
    t = {k: tm.add_topic(fctx, s, k, k.title(), {"source_id": pid, "chunk_no": 0, "quote": k})["id"] for k in ("pacing", "fuel")}
    return fctx, s, t, pid, sid


def reply(qs):
    return json.dumps({"questions": qs})


def made(world, fake_or, qs=None):
    ctx, s, t, pid, sid = world
    p = paper.pair(ctx, pid, sid, subject_id=s)
    fake_or.chat_returns(reply(GOOD if qs is None else qs))
    run(paper.parse(ctx, p["paper_id"]))
    return p["paper_id"]


# ---- the tables ------------------------------------------------------------------------------------------------------
def test_migration_adds_the_tables(fctx):
    for tbl in ("teach_papers", "teach_paper_runs"):
        assert fctx.con.execute(f"select count(*) from {tbl}").fetchone()[0] == 0
    cols = {r[1] for r in fctx.con.execute("pragma table_info(teach_paper_runs)")}
    assert {"paper_id", "status", "minutes", "started_at", "ends_at", "answers_json", "marks_json", "total", "out_of", "estimate_json", "extra"} <= cols


# ---- pairing ---------------------------------------------------------------------------------------------------------
def test_two_files_with_the_same_stem_pair_by_their_names():
    assert paper.which("9702_s22_qp_12", "9702_s22_ms_12") == ("9702_s22_qp_12", "9702_s22_ms_12")
    assert paper.which("Physics MS June", "Physics QP June") == ("Physics QP June", "Physics MS June")
    assert paper.which("notes", "other") is None
    assert paper.which("a_qp_1", "b_ms_2") is None                       # not the same stem


def test_pair_makes_one_row_and_queues_one_parse(world):
    ctx, s, t, pid, sid = world
    a = paper.pair(ctx, pid, sid, subject_id=s)
    b = paper.pair(ctx, pid, sid, subject_id=s)
    assert a["paper_id"] == b["paper_id"]
    assert ctx.con.execute("select count(*) from teach_papers").fetchone()[0] == 1
    assert ctx.con.execute("select count(*) from jobs where kind='teach_paper_parse'").fetchone()[0] == 1
    assert paper.pair(ctx, pid, "nope")["ok"] is False


# ---- parsing ---------------------------------------------------------------------------------------------------------
def test_a_paper_parses_into_questions_with_scheme_points(world, fake_or):
    ctx = world[0]
    pid = made(world, fake_or)
    r = ctx.con.execute("select * from teach_papers where id=?", (pid,)).fetchone()
    d = json.loads(r["questions_json"])
    assert r["status"] == "ready" and [x["no"] for x in d["questions"]] == ["1", "2", "3"] and d["failed"] == []
    assert len(d["questions"][1]["points"]) == 3 and fake_or.request_count == 1


def test_a_point_with_a_made_up_quote_drops_its_question_and_says_so(world, fake_or):
    ctx = world[0]
    bad = json.loads(json.dumps(GOOD))
    bad[1]["points"][2]["quote"] = "never said anywhere in the scheme at all"
    pid = made(world, fake_or, bad)
    d = json.loads(ctx.con.execute("select questions_json from teach_papers where id=?", (pid,)).fetchone()[0])
    assert [x["no"] for x in d["questions"]] == ["1", "3"] and d["failed"] == ["2"]
    assert "could not read question 2" in paper.say_failed(d["failed"])


def test_a_made_up_paper_quote_drops_the_question(world, fake_or):
    ctx = world[0]
    bad = json.loads(json.dumps(GOOD))
    bad[0]["cite"]["quote"] = "a sentence that is not in the paper"
    pid = made(world, fake_or, bad)
    assert json.loads(ctx.con.execute("select questions_json from teach_papers where id=?", (pid,)).fetchone()[0])["failed"] == ["1"]


def test_fewer_points_than_marks_drops_the_question(world, fake_or):
    ctx = world[0]
    bad = json.loads(json.dumps(GOOD))
    bad[1]["points"] = bad[1]["points"][:2]                              # 3 marks but 2 points
    pid = made(world, fake_or, bad)
    assert json.loads(ctx.con.execute("select questions_json from teach_papers where id=?", (pid,)).fetchone()[0])["failed"] == ["2"]


def test_junk_reply_leaves_the_paper_failed_not_ready(world, fake_or):
    ctx = world[0]
    pid = made(world, fake_or, [])
    r = ctx.con.execute("select status from teach_papers where id=?", (pid,)).fetchone()
    assert r["status"] == "failed"


# ---- the clock fit ---------------------------------------------------------------------------------------------------
def test_fit_is_about_a_mark_a_minute_in_paper_order():
    qs = [{"no": str(i), "marks": m} for i, m in enumerate([2, 3, 1, 4], 1)]
    assert [x["no"] for x in paper.fit(qs, 5)] == ["1", "2"]
    assert [x["no"] for x in paper.fit(qs, 6)] == ["1", "2", "3"]
    assert [x["no"] for x in paper.fit(qs, 1)] == ["3"]                   # the first that fits
    assert paper.fit(qs, 0) == []


def test_fit_never_goes_over_the_minutes_property():
    rnd = random.Random(7)
    for _ in range(200):
        qs = [{"no": str(i), "marks": rnd.randint(1, 8)} for i in range(rnd.randint(1, 10))]
        mins = rnd.randint(1, 40)
        got = paper.fit(qs, mins)
        assert sum(x["marks"] for x in got) <= mins
        assert [x["no"] for x in got] == [x["no"] for x in qs if x in got]       # order kept


# ---- a run -----------------------------------------------------------------------------------------------------------
def start(world, fake_or, minutes=6, **kw):
    ctx, s, *_ = world
    made(world, fake_or)
    return run(paper.start(ctx, s, minutes, **kw))


def test_start_asks_the_first_question_and_starts_the_clock(world, fake_or):
    ctx = world[0]
    out = start(world, fake_or, 5)
    assert out["ok"] and out["ask"].startswith("Question 1") and "State what a negative split is" in out["ask"]
    r = paper.current(ctx)
    assert r is not None and r["ends_at"] - r["started_at"] == 300
    assert json.loads(r["extra"])["qnos"] == ["1", "2"]                   # 2 + 3 marks fit 5 minutes, the 1 mark one after would be 6
    t = ctx.con.execute("select * from timers where status='active'").fetchall()
    assert len(t) == 1 and t[0]["ends_at"] == r["ends_at"]
    from misty_engine import chat
    assert chat.teach_pending(ctx) == "paper"


def test_six_minutes_holds_all_three_questions(world, fake_or):
    ctx = world[0]
    start(world, fake_or, 6)
    assert json.loads(paper.current(ctx)["extra"])["qnos"] == ["1", "2", "3"]


def test_pairing_by_his_words_then_a_paper_to_run(world, fake_or):
    ctx, s, t, pid, sid = world
    out = run(live.tool(ctx, {"action": "exam_start", "subject": "running", "minutes": 6, "paper": "9999_qp", "scheme": "9999_ms"}))
    assert out["ok"] is False and "reading the paper" in out["error"]
    assert ctx.con.execute("select count(*) from teach_papers").fetchone()[0] == 1
    assert paper.pair_by_words(ctx, "nothing", "9999_ms")["ok"] is False


def test_no_ready_paper_says_so(world):
    ctx, s, *_ = world
    out = run(paper.start(ctx, s, 10))
    assert out["ok"] is False and "paper" in out["error"]


def test_starting_while_a_lesson_or_drill_or_paper_is_live_is_refused(world, fake_or):
    ctx, s, *_ = world
    out = start(world, fake_or, 6)
    assert out["ok"]
    assert run(paper.start(ctx, s, 6))["ok"] is False                      # one run at a time
    assert "paper" in run(live.start(ctx, world[2]["pacing"]))["error"]
    assert run(drill.start(ctx, s))["ok"] is False


def test_a_live_lesson_blocks_it_and_a_paused_one_is_left(world, fake_or):
    ctx, s, t, *_ = world
    made(world, fake_or)
    ctx.con.execute("PRAGMA foreign_keys=OFF")
    ctx.con.execute("INSERT INTO teach_live(id, topic_id, plan_id, status, started_at, updated_at) VALUES ('lv1',?,'p','live',?,?)",
                    (t["pacing"], ctx.clock.now(), ctx.clock.now()))
    assert run(paper.start(ctx, s, 6))["ok"] is False
    ctx.con.execute("UPDATE teach_live SET status='paused'")
    out = run(paper.start(ctx, s, 6))
    assert out["ok"] and "say carry on" in out["left"]


def test_his_answers_are_stored_and_answered_by_code_with_no_model_call(world, fake_or):
    ctx = world[0]
    start(world, fake_or, 6)
    before = fake_or.request_count
    a = run(paper.on_message(ctx, "the second half is faster"))
    b = run(paper.on_message(ctx, "carbs run out"))
    assert fake_or.request_count == before
    assert a["canned"].startswith("Noted, next.") and "Question 2" in a["canned"]
    assert "Question 3" in b["canned"]
    assert json.loads(paper.current(ctx)["answers_json"]) == {"1": "the second half is faster", "2": "carbs run out"}


def test_skip_moves_on_with_no_answer(world, fake_or):
    ctx = world[0]
    start(world, fake_or, 6)
    out = run(paper.on_message(ctx, "skip"))
    assert "Question 2" in out["canned"]
    assert json.loads(paper.current(ctx)["answers_json"]) == {"1": ""}


# ---- marking ---------------------------------------------------------------------------------------------------------
def fake_marks(monkeypatch, covered_by_q, calls=None):
    async def grade(ctx, question, key, his):
        if calls is not None:
            calls.append(question)
        no = next(x["no"] for x in GOOD if x["text"] == question)
        cov = covered_by_q.get(no, [])
        n = len(key["points"])
        v = "right" if len(set(cov)) >= n else "partly" if cov else "wrong"
        miss = [p["text"] for i, p in enumerate(key["points"]) if i not in cov]
        return {"verdict": v, "why": "missing: " + "; ".join(miss), "covered": cov, "n": n, "cost": 0.0}
    monkeypatch.setattr(keys, "grade", grade)

    async def topic_for(ctx, subject_id, text):
        return ctx.con.execute("select id from teach_topics where key='pacing'").fetchone()[0]
    monkeypatch.setattr(paper.match, "topic_for", topic_for)


def finish_all(ctx, answers=("a", "b", "c")):
    out = None
    for a in answers:
        out = run(paper.on_message(ctx, a))
    return out


def test_marking_gives_marks_per_question_and_where_each_went(world, fake_or, monkeypatch):
    ctx = world[0]
    start(world, fake_or, 6)
    fake_marks(monkeypatch, {"1": [0, 1], "2": [0], "3": []})
    out = finish_all(ctx)
    assert out["finished"] and out["total"] == 3 and out["out_of"] == 6
    assert out["lines"][0].startswith("Q1: 2 of 2") and out["lines"][1].startswith("Q2: 1 of 3") and "legs slow" in out["lines"][1]
    assert out["lines"][2].startswith("Q3: 0 of 1")
    r = ctx.con.execute("select * from teach_paper_runs").fetchone()
    assert r["status"] == "done" and r["total"] == 3 and r["out_of"] == 6
    m = json.loads(r["marks_json"])
    assert m["2"]["got"] == 1 and m["2"]["covered"] == ["stores run out"] and "legs slow" in m["2"]["missed"]


def test_marks_never_go_above_the_questions_marks(world, fake_or, monkeypatch):
    ctx = world[0]
    start(world, fake_or, 6)
    fake_marks(monkeypatch, {"1": [0, 1, 2, 3, 4], "2": [0, 1, 2, 9], "3": [0, 0, 0]})
    out = finish_all(ctx)
    r = ctx.con.execute("select total, out_of from teach_paper_runs").fetchone()
    assert r["total"] <= r["out_of"] == 6 and out["lines"][0].startswith("Q1: 2 of 2") and out["lines"][2].startswith("Q3: 1 of 1")


def test_a_right_answer_writes_no_evidence_and_a_wrong_one_writes_exam_evidence_and_a_timed_slip(world, fake_or, monkeypatch):
    ctx, s, t, *_ = world
    start(world, fake_or, 6)
    fake_marks(monkeypatch, {"1": [0, 1], "2": [0], "3": []})
    finish_all(ctx, ("good", "half", "wrong"))
    ev = ctx.con.execute("select kind, origin from teach_evidence where origin='exam' order by id").fetchall()
    assert sorted(e["kind"] for e in ev) == ["partly", "wrong"]
    sl = ctx.con.execute("select * from teach_slips order by at, id").fetchall()
    assert len(sl) == 2 and all(x["timed"] == 1 and x["evidence_id"] and json.loads(x["key_json"])["points"] for x in sl)
    assert {x["his_answer"] for x in sl} == {"half", "wrong"}
    assert ctx.con.execute("select count(*) from jobs where kind='teach_slip_label'").fetchone()[0] == 2


def test_the_cost_of_marking_is_one_call_per_answered_question(world, fake_or, monkeypatch):
    ctx = world[0]
    start(world, fake_or, 6)
    calls = []
    fake_marks(monkeypatch, {"1": [0, 1]}, calls)
    run(paper.on_message(ctx, "x"))
    run(paper.on_message(ctx, "skip"))
    run(paper.on_message(ctx, "z"))
    assert len(calls) == 2                                                # the skipped one is never sent to the model


def test_the_points_grader_reports_which_points_it_found(world, fake_or):
    ctx = world[0]
    fake_or.chat_returns(json.dumps({"covered": [0, 2]}))
    key = {"points": [{"text": "a", "quote": "x y z"}, {"text": "b", "quote": "x y z"}, {"text": "c", "quote": "x y z"}]}
    g = run(keys.grade(ctx, "Q?", key, "his words"))
    assert g["verdict"] == "partly" and g["covered"] == [0, 2] and g["n"] == 3


def test_exam_end_stops_and_marks_what_was_answered(world, fake_or, monkeypatch):
    ctx = world[0]
    start(world, fake_or, 6)
    run(paper.on_message(ctx, "my answer"))
    fake_marks(monkeypatch, {"1": [0]})
    out = run(paper.end(ctx))
    assert out["finished"] and out["out_of"] == 6
    assert ctx.con.execute("select status from teach_paper_runs").fetchone()[0] == "stopped"
    assert paper.current(ctx) is None and ctx.con.execute("select count(*) from timers where status='active'").fetchone()[0] == 0


def test_exam_end_with_nothing_running(world):
    assert run(paper.end(world[0]))["ok"] is False


def test_the_timer_ending_ends_the_run_and_marks_it(world, fake_or, monkeypatch, clock):
    ctx = world[0]
    start(world, fake_or, 6)
    run(paper.on_message(ctx, "in time"))
    clock.advance(6 * 60 + 5)
    fake_marks(monkeypatch, {"1": [0, 1]})
    out = run(paper.on_message(ctx, "too late"))                           # after the clock: not counted
    assert out["finished"] and out["timed_out"]
    r = ctx.con.execute("select * from teach_paper_runs").fetchone()
    assert r["status"] == "timeout" and json.loads(r["answers_json"]) == {"1": "in time"} and r["total"] == 2


def test_tick_marks_a_run_nobody_came_back_to(world, fake_or, monkeypatch, clock):
    ctx = world[0]
    start(world, fake_or, 6)
    run(paper.on_message(ctx, "one"))
    assert run(paper.tick(ctx)) is None                                    # still running
    clock.advance(6 * 60 + 10 * 60)
    fake_marks(monkeypatch, {"1": [0]})
    assert run(paper.tick(ctx)) is not None
    assert ctx.con.execute("select status from teach_paper_runs").fetchone()[0] == "timeout"


# ---- the estimate ----------------------------------------------------------------------------------------------------
def add_run(ctx, paper_id, pct, n=0):
    ctx.con.execute("INSERT INTO teach_paper_runs(id, paper_id, status, minutes, started_at, ends_at, total, out_of) VALUES (?,?,?,?,?,?,?,?)",
                    (f"r{n}{pct}", paper_id, "done", 10, ctx.clock.now() + n, ctx.clock.now() + n, pct, 100))


def test_no_grade_without_the_bounds_file(world, fake_or):
    ctx, s, *_ = world
    pid = made(world, fake_or)
    for i, p in enumerate((60, 70, 80)):
        add_run(ctx, pid, p, i)
    e = paper.estimate(ctx, s)
    assert e["grade"] is None and "bounds" in e["why"]


def test_no_grade_with_fewer_than_three_runs(world, fake_or):
    ctx, s, *_ = world
    ctx.paths.home.joinpath("teach").mkdir(exist_ok=True)
    ctx.paths.home.joinpath("teach", "running-bounds.yaml").write_text("7: 80\n6: 70\n5: 60\n4: 50\n")
    pid = made(world, fake_or)
    add_run(ctx, pid, 60, 0)
    add_run(ctx, pid, 90, 1)
    e = paper.estimate(ctx, s)
    assert e["grade"] is None and "3" in e["why"]


def test_a_range_from_the_last_three_runs_through_his_bounds(world, fake_or):
    ctx, s, *_ = world
    ctx.paths.home.joinpath("teach").mkdir(exist_ok=True)
    ctx.paths.home.joinpath("teach", "running-bounds.yaml").write_text("7: 80\n6: 70\n5: 60\n4: 50\n")
    pid = made(world, fake_or)
    for i, p in enumerate((20, 62, 75, 81)):                                # the oldest (20) is not one of the last 3
        add_run(ctx, pid, p, i)
    e = paper.estimate(ctx, s)
    assert e["grade"] == "5 to 7" and "estimate" in e["say"].lower()


def test_a_bounds_file_that_is_junk_gives_no_grade(world, fake_or):
    ctx, s, *_ = world
    ctx.paths.home.joinpath("teach").mkdir(exist_ok=True)
    ctx.paths.home.joinpath("teach", "running-bounds.yaml").write_text("not: [a, mapping of numbers")
    pid = made(world, fake_or)
    for i, p in enumerate((60, 70, 80)):
        add_run(ctx, pid, p, i)
    assert paper.estimate(ctx, s)["grade"] is None


# ---- the tool and the chat -------------------------------------------------------------------------------------------
def test_tool_dispatch_exam_start_and_end(world, fake_or, monkeypatch):
    ctx, s, *_ = world
    made(world, fake_or)
    out = run(live.tool(ctx, {"action": "exam_start", "subject": "running", "minutes": 6}))
    assert out["ok"] and "Question 1" in out["ask"]
    assert run(live.tool(ctx, {"action": "exam_start", "subject": "running", "minutes": 6}))["ok"] is False
    fake_marks(monkeypatch, {})
    assert run(live.tool(ctx, {"action": "exam_end"}))["finished"]


def test_tool_needs_minutes_and_a_known_subject(world):
    ctx = world[0]
    assert run(live.tool(ctx, {"action": "exam_start", "subject": "running"}))["ok"] is False
    assert "subject" in run(live.tool(ctx, {"action": "exam_start", "subject": "zzz", "minutes": 5}))["error"]


def test_stop_while_a_paper_is_live_ends_the_paper(world, fake_or, monkeypatch):
    ctx = world[0]
    start(world, fake_or, 6)
    fake_marks(monkeypatch, {})
    assert run(live.tool(ctx, {"action": "stop"}))["finished"]


def test_live_hook_routes_his_message_to_the_paper(world, fake_or):
    ctx = world[0]
    start(world, fake_or, 6)
    out = run(live.on_message(ctx, "an answer"))
    assert out["canned"].startswith("Noted, next.")


def test_chat_schema_has_the_actions_and_args():
    from misty_engine import chat
    t = next(x for x in chat.TOOLS if x["function"]["name"] == "teach")["function"]["parameters"]["properties"]
    assert {"exam_start", "exam_end"} <= set(t["action"]["enum"]) and "minutes" in t and "paper" in t
    assert "exam_start" in chat.TEACH_START_RULE


def test_a_live_paper_rule_says_to_say_nothing_about_answers(world, fake_or):
    ctx = world[0]
    start(world, fake_or, 6)
    block = live.rule_block(ctx)
    assert "paper" in block.lower() and "nothing" in block.lower()


def test_no_model_call_outside_the_one_door():
    import inspect
    src = inspect.getsource(paper)
    assert "ctx.openrouter" not in src and "ctx.runner" not in src and ".post(" not in src
