"""E17: before and after. Three sets of three bank questions, one per level, asked as a quiet drill. Fake model, term keys graded by code. No paid call."""
import asyncio
import itertools
import json
import random
import sys
from pathlib import Path

import pytest

from misty_engine.teach import bank, drill, live, plan, probe, sources
from misty_engine.teach import map as tm

DAY = 86400
PASSAGE = "A negative split is when the second half of a run is faster than the first half of the run."
_ids = itertools.count(1)


def run(coro):
    return asyncio.run(coro)


def add_q(ctx, topic, level=1, source="bank", status="usable"):
    n = next(_ids)
    qid, word = f"pq{n}", f"word{n}"
    ctx.con.execute("INSERT INTO teach_questions(id, topic_id, level, q, key_json, cite_json, source, status, made_at, uses) VALUES (?,?,?,?,?,?,?,?,?,0)",
                    (qid, topic, level, f"Probe question {n} here?", json.dumps({"accept": [word]}), "{}", source, status, ctx.clock.now() + n))
    return qid, word


def fill(ctx, topic, per=3, levels=(1, 2, 3)):
    return [add_q(ctx, topic, lv)[0] for lv in levels for _ in range(per)]


def cite(src):
    return {"source_id": src, "chunk_no": 0, "quote": PASSAGE[:80]}


def make_plan(src, n=2):
    def item(q, word):
        return {"q": q, "key": {"accept": [word]}, "hints": ["h1", "h2", "h3"], "cite": cite(src)}
    steps = [{"idea": f"idea {i}", "check": item(f"Question {i}?", f"ans{i}"),
              "explain": [{"way": w, "say": f"say {w} {i}", "board": None, "cite": cite(src)} for w in plan.WAYS]} for i in range(n)]
    return {"steps": steps, "guided": item("Guided question?", "gans"), "solo": item("Solo question?", "sans"), "recap": "the recap words"}


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
    fctx.con.execute("INSERT INTO teach_plans(id, topic_id, version, plan_json, model, cost, status, why, made_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("tp_" + t[-6:], t, 1, json.dumps(make_plan(src)), "haiku", 0.0, "usable", "", fctx.clock.now()))
    return t


def say(ctx, text):
    return run(live.on_message(ctx, text))


def word_for(ctx, qid):
    return json.loads(ctx.con.execute("SELECT key_json FROM teach_questions WHERE id=?", (qid,)).fetchone()[0])["accept"][0]


def cur_word(ctx):
    r = ctx.con.execute("SELECT * FROM teach_drill WHERE status='live'").fetchone()
    return word_for(ctx, json.loads(r["qids"])[r["i"]])


def finish_lesson(ctx):
    for _ in range(2):
        r = live.current(ctx)
        s = live._state(r)
        say(ctx, live._item(live._plan(ctx, r), s["phase"], s["step"])["key"]["accept"][0])
    say(ctx, "gans")
    return say(ctx, "sans")


def probe_rows(ctx):
    return [(r["set_name"], r["status"], r["score"], r["out_of"]) for r in ctx.con.execute("SELECT * FROM teach_probes ORDER BY at, rowid")]


def answer_probe(ctx, rights):
    """Answer a running probe: rights is a list of booleans. Returns the last result."""
    res = None
    for ok in rights:
        res = say(ctx, cur_word(ctx) if ok else "banana split zzz")
    return res


# ---- the table -------------------------------------------------------------------------------------------------------
def test_table_exists(fctx):
    cols = {r[1] for r in fctx.con.execute("pragma table_info(teach_probes)")}
    assert {"id", "topic_id", "set_name", "qids", "score", "out_of", "lesson_id", "at", "status"} <= cols


# ---- the sets: property tests over random banks ----------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(25))
def test_three_sets_never_share_and_have_one_per_level(fctx, seed):
    rnd = random.Random(seed)
    s = tm.add_subject(fctx, f"sub{seed}", "Sub")["id"]
    t = tm.add_topic(fctx, s, "t", "T", {"source_id": "x", "chunk_no": 0, "quote": "t"})["id"]
    for lv in (1, 2, 3):
        for _ in range(rnd.randint(3, 8)):
            add_q(fctx, t, lv)
    for _ in range(rnd.randint(0, 6)):
        add_q(fctx, t, rnd.choice((1, 2, 3)), source="plan")                 # plan questions are never probed
    for _ in range(rnd.randint(0, 4)):
        add_q(fctx, t, rnd.choice((1, 2, 3)), status=rnd.choice(("bad", "retired")))
    seen = set()
    for name in probe.SETS:
        qids = probe.pick_set(fctx, t, name)
        assert qids is not None and len(qids) == 3
        rows = [fctx.con.execute("SELECT * FROM teach_questions WHERE id=?", (q,)).fetchone() for q in qids]
        assert sorted(r["level"] for r in rows) == [1, 2, 3]
        assert all(r["source"] == "bank" and r["status"] == "usable" for r in rows)
        assert not (set(qids) & seen)
        seen |= set(qids)
        probe._put(fctx, t, name, qids, "done", "", 2, 3)                      # asked: the next set must avoid it


@pytest.mark.parametrize("have", [(3, 3, 2), (3, 2, 3), (8, 0, 0), (2, 2, 2), (0, 0, 0), (3, 3, 0)])
def test_under_nine_usable_gets_no_probe(fctx, have):
    s = tm.add_subject(fctx, "s", "S")["id"]
    t = tm.add_topic(fctx, s, "t", "T", {"source_id": "x", "chunk_no": 0, "quote": "t"})["id"]
    for lv, n in zip((1, 2, 3), have):
        for _ in range(n):
            add_q(fctx, t, lv)
    for _ in range(5):
        add_q(fctx, t, 2, source="plan")
    assert not probe.eligible(fctx, t)
    assert probe.pick_set(fctx, t, "before") is None
    assert probe.offer_before(fctx, t) is None
    assert drill.begin_probe(fctx, t, "before", [])["ok"] is False


def test_a_plan_question_in_a_set_is_refused_by_the_guard(fctx):
    s = tm.add_subject(fctx, "s", "S")["id"]
    t = tm.add_topic(fctx, s, "t", "T", {"source_id": "x", "chunk_no": 0, "quote": "t"})["id"]
    good = fill(fctx, t)
    p1, _ = add_q(fctx, t, 1, source="plan")
    assert probe.valid(fctx, t, "before", [good[0], good[3], good[6]])
    assert not probe.valid(fctx, t, "before", [p1, good[3], good[6]])           # a plan question
    assert not probe.valid(fctx, t, "before", [good[0], good[1], good[6]])      # two of one level
    assert not probe.valid(fctx, t, "before", [good[0], good[3]])               # too few
    probe._put(fctx, t, "after", [good[0], good[3], good[6]], "done", "", 1, 3)
    assert not probe.valid(fctx, t, "before", [good[0], good[4], good[7]])      # shared with another set


def test_a_bad_question_never_gets_into_a_set(fctx):
    s = tm.add_subject(fctx, "s", "S")["id"]
    t = tm.add_topic(fctx, s, "t", "T", {"source_id": "x", "chunk_no": 0, "quote": "t"})["id"]
    ids = fill(fctx, t, per=4)
    first = probe.pick_set(fctx, t, "before")
    bank.set_status(fctx, first[0], "bad")
    again = probe.pick_set(fctx, t, "before")
    assert first[0] not in again and len(again) == 3 and set(again) <= set(ids)


# ---- before: offer, yes, skip ----------------------------------------------------------------------------------------
def test_start_on_an_untaught_topic_offers_the_probe_first(fctx, topic):
    fill(fctx, topic)
    r = run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    assert r["ok"] and r["probe_offer"]["set"] == "before" and "just teach me" in r["say"]
    assert live.current(fctx) is None and probe_rows(fctx) == [("before", "offered", 0, 0)]


def test_a_topic_with_a_small_bank_just_starts_the_lesson(fctx, topic):
    fill(fctx, topic, per=2)
    r = run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    assert r["ok"] and "probe_offer" not in r and r["first"] and probe_rows(fctx) == []


def test_a_known_topic_gets_no_before_offer(fctx, topic):
    fill(fctx, topic)
    tm.record(fctx, topic, "wrong", "drill", "seed")                               # shaky already
    r = run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    assert "probe_offer" not in r and r["first"]


def test_yes_runs_the_before_probe_then_the_lesson_starts_by_itself(fctx, topic):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    r = say(fctx, "yes")
    assert r["ok"] and r["drill"]["of"] == 3 and r["do"][-1]["ask"].startswith("Probe question")
    assert fctx.con.execute("SELECT kind FROM teach_drill WHERE status='live'").fetchone()[0] == "probe"
    r = answer_probe(fctx, [False, True, True][:2])
    assert r["do"][0]["verdict"] == "right"
    r = say(fctx, cur_word(fctx))
    assert r["probe"] == {"set": "before", "right": 2, "of": 3} and r["do"][0]["verdict"] == "right"
    assert r["lesson_started"]["ok"] and r["lesson_started"]["first"] and live.current(fctx) is not None
    assert probe_rows(fctx) == [("before", "done", 2, 3)]
    assert "line" not in r["probe"]


def test_a_probe_is_quiet_no_hint_no_reveal_no_reason_no_mistakes_book(fctx, topic):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    say(fctx, "yes")
    ans = cur_word(fctx)
    r = say(fctx, "banana split zzz")
    assert r["do"][0]["verdict"] == "wrong" and "why" not in r["do"][0]
    assert not [d for d in r["do"] if "reveal" in d] and ans not in json.dumps(r)
    r = say(fctx, "show me the answer")                                            # asking for a hint in a test: wrong, nothing shown
    assert not [d for d in r["do"] if "reveal" in d]
    assert fctx.con.execute("SELECT COUNT(*) FROM teach_slips").fetchone()[0] == 0
    notes = {x["note"] for x in fctx.con.execute("SELECT note FROM teach_evidence WHERE origin='drill'")}
    assert notes == {"probe:before"}


def test_probe_answers_write_evidence_that_moves_the_level(fctx, topic):
    fill(fctx, topic)
    assert tm.level(fctx, topic, fctx.clock.now()) == "unknown"
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    say(fctx, "yes")
    answer_probe(fctx, [True, True, True])
    kinds = [r["kind"] for r in fctx.con.execute("SELECT kind FROM teach_evidence WHERE origin='drill' ORDER BY id")]
    assert kinds == ["right_alone"] * 3 and tm.level(fctx, topic, fctx.clock.now()) != "unknown"


@pytest.mark.parametrize("words", ["just teach me", "skip", "no", "nope", "not now"])
def test_skipping_writes_skipped_and_starts_the_lesson(fctx, topic, words):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    r = say(fctx, words)
    assert r["ok"] and r["probe_skipped"] and r["first"] and live.current(fctx) is not None
    assert probe_rows(fctx) == [("before", "skipped", 0, 0)]
    assert not fctx.con.execute("SELECT 1 FROM teach_drill").fetchall()
    out = finish_lesson(fctx)
    assert "probe_offer" in out                                                      # the after is still offered
    assert probe.offer_before(fctx, topic) is None                                   # and the before is not offered again


def test_the_line_after_a_skipped_before_says_only_now_never_learned(fctx, topic):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    say(fctx, "just teach me")
    finish_lesson(fctx)
    say(fctx, "yes")
    r = answer_probe(fctx, [True, True, True])
    assert r["probe"]["line"] == "3 of 3 now." and "learn" not in r["probe"]["line"].lower()


def test_an_offer_he_ignores_is_dropped_after_half_an_hour(fctx, topic, clock):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    clock.advance(31 * 60)
    assert say(fctx, "yes") is None and probe_rows(fctx) == [("before", "offered", 0, 0)]


def test_other_words_are_not_an_answer_to_the_offer(fctx, topic):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    assert say(fctx, "what is the weather like in singapore today please") is None
    assert probe_rows(fctx) == [("before", "offered", 0, 0)]


def test_stopping_the_before_skips_it_and_no_lesson_starts(fctx, topic):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    say(fctx, "yes")
    r = say(fctx, "stop")
    assert r["probe"]["stopped"] and live.current(fctx) is None and probe_rows(fctx) == [("before", "skipped", 0, 0)]


# ---- after -----------------------------------------------------------------------------------------------------------
def test_after_is_offered_when_the_lesson_is_done_and_the_line_has_both(fctx, topic):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    say(fctx, "yes")
    answer_probe(fctx, [False, False, True])
    out = finish_lesson(fctx)
    assert out["probe_offer"]["set"] == "after" and "next drill" in out["probe_offer"]["say"]
    r = say(fctx, "yes")
    assert r["ok"] and r["drill"]["of"] == 3
    r = answer_probe(fctx, [True, True, True])
    assert r["probe"]["line"] == "1 of 3 before, 3 of 3 now."
    assert [x[:2] for x in probe_rows(fctx)] == [("before", "done"), ("after", "done")]
    sets = [set(json.loads(x["qids"])) for x in fctx.con.execute("SELECT qids FROM teach_probes")]
    assert not (sets[0] & sets[1])


def test_not_now_leaves_the_after_for_the_next_drill(fctx, topic):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    say(fctx, "just teach me")
    finish_lesson(fctx)
    r = say(fctx, "not now")
    assert r["ok"] and r["probe"]["left"] and probe_rows(fctx)[-1][:2] == ("after", "left")
    d = run(live.tool(fctx, {"action": "drill", "topic": "negative"}))              # the next drill on that topic is the after probe
    assert d["ok"] and d["drill"]["of"] == 3
    assert fctx.con.execute("SELECT kind FROM teach_drill WHERE status='live'").fetchone()[0] == "probe"
    r = answer_probe(fctx, [True, False, True])
    assert r["probe"]["set"] == "after" and r["probe"]["line"] == "2 of 3 now."
    assert [x[:2] for x in probe_rows(fctx)][-1] == ("after", "done") and len(probe_rows(fctx)) == 2


def test_a_left_after_is_forgotten_after_three_days(fctx, topic, clock):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    say(fctx, "just teach me")
    finish_lesson(fctx)
    say(fctx, "not now")
    clock.advance(4 * DAY)
    assert probe.for_drill(fctx, topic) is None


def test_a_small_bank_gets_no_after_offer(fctx, topic):
    fill(fctx, topic, per=2)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    out = finish_lesson(fctx)
    assert "probe_offer" not in out and probe_rows(fctx) == []


# ---- week ------------------------------------------------------------------------------------------------------------
def _taught(ctx, topic):
    run(live.tool(ctx, {"action": "start", "topic": "negative"}))
    say(ctx, "yes")
    answer_probe(ctx, [False, True, False])
    finish_lesson(ctx)
    say(ctx, "yes")
    answer_probe(ctx, [True, True, True])


def test_week_probe_rides_the_drill_on_the_topic_six_days_after_the_after(fctx, topic, clock):
    fill(fctx, topic, per=4)
    _taught(fctx, topic)
    assert not probe.week_due(fctx, topic)
    clock.advance(5 * DAY)
    assert not probe.week_due(fctx, topic)
    clock.advance(2 * DAY)
    assert probe.week_due(fctx, topic)
    d = run(live.tool(fctx, {"action": "drill", "topic": "negative"}))
    assert d["ok"] and d["drill"]["of"] == 3
    assert fctx.con.execute("SELECT kind FROM teach_drill WHERE status='live'").fetchone()[0] == "probe"
    r = answer_probe(fctx, [True, True, False])
    assert r["probe"]["set"] == "week" and r["probe"]["line"] == "1 of 3 before, 3 of 3 now. 2 of 3 a week later."
    assert not probe.week_due(fctx, topic)
    sets = [set(json.loads(x["qids"])) for x in fctx.con.execute("SELECT qids FROM teach_probes WHERE status='done'")]
    assert len(sets) == 3 and not (sets[0] & sets[1]) and not (sets[1] & sets[2]) and not (sets[0] & sets[2])


def test_an_ordinary_drill_is_not_touched_when_no_probe_is_due(fctx, topic):
    fill(fctx, topic)
    tm.record(fctx, topic, "wrong", "drill", "seed")
    d = run(live.tool(fctx, {"action": "drill", "topic": "negative"}))
    assert d["ok"] and d["drill"]["of"] == 5
    assert fctx.con.execute("SELECT kind FROM teach_drill WHERE status='live'").fetchone()[0] == "drill"


# ---- the rule the model sees -----------------------------------------------------------------------------------------
def test_the_model_rule_during_a_probe_is_the_quiet_one(fctx, topic):
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    say(fctx, "yes")
    block = live.rule_block(fctx)
    assert "quick check" in block and "never the answer" in block


# ---- ten scripted lessons with probes (the bar is 9) -----------------------------------------------------------------
SCRIPTS = [
    ("yes", [True, True, True], "yes", [True, True, True]),
    ("yes", [False, False, False], "yes", [True, True, True]),
    ("yes", [True, False, True], "yes", [True, False, True]),
    ("skip", None, "yes", [True, True, True]),
    ("skip", None, "not now", None),
    ("yes", [False, True, False], "not now", None),
    ("yes", [True, True, True], "yes", [False, False, False]),
    ("just teach me", None, "yes", [True, True, False]),
    ("yes", [False, False, True], "yes", [True, True, True]),
    ("no", None, "yes", [True, False, False]),
]


@pytest.mark.parametrize("n,script", list(enumerate(SCRIPTS)))
def test_scripted_lesson_with_probes_ends_in_the_right_state(fctx, topic, n, script):
    first, b_rights, second, a_rights = script
    fill(fctx, topic)
    run(live.tool(fctx, {"action": "start", "topic": "negative"}))
    say(fctx, first)
    if b_rights is not None:
        answer_probe(fctx, b_rights)
    assert live.current(fctx) is not None                                               # the lesson began either way
    out = finish_lesson(fctx)
    assert out["probe_offer"]["set"] == "after" and live.current(fctx) is None
    say(fctx, second)
    rows = [x[:2] for x in probe_rows(fctx)]
    before = ("before", "done") if b_rights is not None else ("before", "skipped")
    if a_rights is None:
        assert rows == [before, ("after", "left")]
        return
    r = answer_probe(fctx, a_rights)
    assert rows == [before, ("after", "offered")] and [x[:2] for x in probe_rows(fctx)] == [before, ("after", "done")]
    done = {x[0]: x for x in probe_rows(fctx)}
    assert done["after"][2:] == (sum(a_rights), 3)
    want = f"{sum(a_rights)} of 3 now." if b_rights is None else f"{sum(b_rights)} of 3 before, {sum(a_rights)} of 3 now."
    assert r["probe"]["line"] == want
    assert fctx.con.execute("SELECT status FROM teach_live").fetchone()[0] == "done"
    assert not fctx.con.execute("SELECT 1 FROM teach_drill WHERE status IN ('live','paused')").fetchall()


# ---- the report ------------------------------------------------------------------------------------------------------
def _report():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "checks"))
    import learning_report
    return learning_report


def test_learning_report_counts_only_topics_with_both_sets(fctx):
    s = tm.add_subject(fctx, "s", "S")["id"]
    mk = lambda k: tm.add_topic(fctx, s, k, k.title(), {"source_id": "x", "chunk_no": 0, "quote": k})["id"]
    a, b, c, d = mk("a"), mk("b"), mk("c"), mk("d")
    fill(fctx, a)
    fill(fctx, b)
    fill(fctx, c, per=2)
    probe._put(fctx, a, "before", [], "done", "", 1, 3)
    probe._put(fctx, a, "after", [], "done", "", 3, 3)
    probe._put(fctx, a, "week", [], "done", "", 2, 3)
    probe._put(fctx, b, "before", [], "skipped")
    probe._put(fctx, b, "after", [], "done", "", 2, 3)
    probe._put(fctx, d, "before", [], "done", "", 0, 3)
    probe._put(fctx, d, "after", [], "done", "", 2, 3)
    rep = _report().build(fctx.con)
    assert rep["counted"] == 2 and rep["skipped_before"] == 1 and rep["small_bank"] == 1
    assert rep["before_avg"] == pytest.approx((1 / 3 + 0) / 2) and rep["after_avg"] == pytest.approx((1 + 2 / 3) / 2)
    assert rep["week_avg"] == pytest.approx(2 / 3) and rep["week_n"] == 1
    text = _report().render(rep)
    assert "1 of 3" in text and "skipped" in text.lower()
