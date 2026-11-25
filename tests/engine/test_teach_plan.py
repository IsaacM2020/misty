"""E5: the lesson plan. A fake model only (ask.think is replaced), no paid call. A made-up stranger's subject.
Keys are `term` keys so E6's checker runs for real without a model."""
import asyncio
import copy
import json

import pytest

from misty_engine import pipeline
from misty_engine.errors import MistyError
from misty_engine.teach import ask, keys, plan, sources
from misty_engine.teach import map as tm

PARAS = [
    "Pacing means spreading your effort evenly over a whole run so you do not tire early.",
    "A negative split is when the second half of a run is faster than the first half of the run.",
    "Fuelling means eating carbohydrate during long runs so that the legs keep working well.",
    "Recovery days let the muscles rebuild after hard work and the next hard run feels better.",
    "Hydration matters because sweat takes salt and water out of the body on a warm day.",
]
QUOTES = {
    "a": "A negative split is when the second half of a run is faster than the first half",
    "b": "Pacing means spreading your effort evenly over a whole run so you do not tire early",
    "c": "Fuelling means eating carbohydrate during long runs so that the legs keep working well",
}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def src(fctx):
    sid = sources._keep(fctx, "page", "https://x.test/run", "run", "x")["id"]
    fctx.con.execute("DELETE FROM teach_chunks WHERE source_id=?", (sid,))
    for i, p in enumerate(PARAS):
        fctx.con.execute("INSERT INTO teach_chunks(source_id, chunk_no, text_scrubbed) VALUES (?,?,?)", (sid, i, p))
    return sid


@pytest.fixture
def topic(fctx, src):
    s = tm.add_subject(fctx, "running", "Running")["id"]
    t = tm.add_topic(fctx, s, "negative split", "Negative splits", {"source_id": src, "chunk_no": 1, "quote": QUOTES["a"]})
    return t["id"]


def cite(src, no=1, q="a"):
    return {"source_id": src, "chunk_no": no, "quote": QUOTES[q]}


def term_key(word="faster"):
    return {"accept": [word]}


def way(name, src, say=None):
    return {"way": name, "say": say or f"Here is the {name} way to see a negative split.", "cite": cite(src)}


def step(src, n, good_check=True, **kw):
    return {"idea": f"Idea {n}.", "explain": [way("plain", src), way("picture", src), way("example", src)],
            "check": {"q": f"Check question {n}?", "key": term_key("faster" if good_check else "slower"), "hints": ["h1", "h2", "h3"], "cite": cite(src)}, **kw}


def good_plan(src, n=4):
    return {"steps": [step(src, i) for i in range(n)],
            "guided": {"q": "Guided: which half is faster?", "key": term_key(), "hints": ["a", "b", "c"], "cite": cite(src)},
            "solo": {"q": "Solo: name the kind of run?", "key": term_key("negative split"), "cite": cite(src)},
            "recap": "A negative split is a faster second half. Start slow and finish strong."}


class Model:
    """ask.think replaced: hands out the queued replies and records what it was given."""
    def __init__(self, *replies, cost=0.004):
        self.replies, self.cost, self.calls = list(replies), cost, []

    async def __call__(self, ctx, system, user, **kw):
        self.calls.append({"system": system, "user": user, **kw})
        r = self.replies.pop(0) if self.replies else self.replies_last
        self.replies_last = r
        if isinstance(r, Exception):
            raise r
        return (r if isinstance(r, str) else json.dumps(r)), self.cost


def go(ctx, topic, monkeypatch, *replies, **kw):
    m = Model(*replies)
    monkeypatch.setattr(ask, "think", m)
    r = run(plan.make(ctx, topic, **kw))
    return r, m


# ---- the table ----------------------------------------------------------------------------------------------------
def test_table_exists(fctx):
    cols = [r[1] for r in fctx.con.execute("pragma table_info(teach_plans)")]
    for c in ("id", "topic_id", "version", "plan_json", "model", "cost", "status", "made_at"):
        assert c in cols


# ---- a good plan ---------------------------------------------------------------------------------------------------
def test_good_plan_is_usable_and_saved(fctx, src, topic, monkeypatch):
    r, m = go(fctx, topic, monkeypatch, good_plan(src))
    assert r["status"] == "usable" and r["version"] == 1 and len(m.calls) == 1
    p = plan.get(fctx, topic)
    assert p["status"] == "usable" and len(p["plan"]["steps"]) == 4
    for st in p["plan"]["steps"]:
        assert [w["way"] for w in st["explain"]] == ["plain", "picture", "example"] and st["check"]
    assert p["plan"]["guided"]["q"] and p["plan"]["solo"]["q"] and p["plan"]["recap"]
    assert abs(p["cost"] - 0.004) < 1e-9 and p["model"] == "haiku"


def test_model_sees_the_chunk_and_two_each_side_and_his_ways(fctx, src, topic, monkeypatch):
    fctx.con.execute("UPDATE teach_topics SET chunk_no=2 WHERE id=?", (topic,))
    r, m = go(fctx, topic, monkeypatch, good_plan(src), ways=["short, with a story"])
    u = m.calls[0]["user"]
    assert all(f"[{i}]" in u for i in range(5)) and "short, with a story" in u and "Negative splits" in u
    fctx.con.execute("UPDATE teach_topics SET chunk_no=0 WHERE id=?", (topic,))
    _, m = go(fctx, topic, monkeypatch, good_plan(src))
    assert "[3]" not in m.calls[0]["user"] and "[2]" in m.calls[0]["user"]
    assert "data" in m.calls[0]["system"].lower() and m.calls[0]["thinking"] is True


# ---- cites ---------------------------------------------------------------------------------------------------------
def test_a_made_up_quote_rejects_and_it_is_made_again_once(fctx, src, topic, monkeypatch):
    bad = good_plan(src)
    bad["steps"][1]["explain"][0]["cite"]["quote"] = "a sentence the source never said at all anywhere"
    r, m = go(fctx, topic, monkeypatch, bad, bad)
    assert r["status"] == "rejected" and "cite" in r["why"] and len(m.calls) == 2      # made again once, then left
    assert "cite" in m.calls[1]["user"].lower()                                      # the second try is told what failed
    rows = fctx.con.execute("select status, version from teach_plans order by version").fetchall()
    assert [(x[0], x[1]) for x in rows] == [("rejected", 1), ("rejected", 2)]
    assert plan.get(fctx, topic) is None


def test_second_try_can_pass(fctx, src, topic, monkeypatch):
    bad = good_plan(src)
    bad["solo"]["cite"]["quote"] = "never said in the source text at all, honestly"
    r, m = go(fctx, topic, monkeypatch, bad, good_plan(src))
    assert r["status"] == "usable" and r["version"] == 2 and len(m.calls) == 2
    assert abs(r["cost"] - 0.008) < 1e-9                                            # both tries are paid for


def test_a_cite_outside_the_window_is_not_a_cite(fctx, src, topic, monkeypatch):
    fctx.con.execute("UPDATE teach_topics SET chunk_no=0 WHERE id=?", (topic,))
    bad = good_plan(src)
    bad["steps"][0]["explain"][0]["cite"] = {"source_id": src, "chunk_no": 4, "quote": PARAS[4][:60]}   # a real quote, but far away
    r, _ = go(fctx, topic, monkeypatch, bad, bad)
    assert r["status"] == "rejected"


# ---- keys ----------------------------------------------------------------------------------------------------------
def test_failed_check_key_makes_the_step_explain_only(fctx, src, topic, monkeypatch):
    p = good_plan(src, 4)
    p["steps"][2] = step(src, 2, good_check=False)
    r, _ = go(fctx, topic, monkeypatch, p)
    assert r["status"] == "usable"
    st = plan.get(fctx, topic)["plan"]["steps"]
    assert len(st) == 4 and st[2]["check"] is None and len(st[2]["explain"]) == 3 and all(s["check"] for i, s in enumerate(st) if i != 2)


def test_fewer_than_three_good_checks_rejects(fctx, src, topic, monkeypatch):
    p = good_plan(src, 4)
    p["steps"][0] = step(src, 0, good_check=False)
    p["steps"][1] = step(src, 1, good_check=False)
    r, _ = go(fctx, topic, monkeypatch, p, p)
    assert r["status"] == "rejected" and "check" in r["why"]


@pytest.mark.parametrize("which", ["guided", "solo"])
def test_guided_or_solo_key_failing_rejects(fctx, src, topic, monkeypatch, which):
    p = good_plan(src)
    p[which]["key"] = term_key("slower")
    r, _ = go(fctx, topic, monkeypatch, p, p)
    assert r["status"] == "rejected" and which in r["why"]


def test_guided_and_solo_must_be_new_questions(fctx, src, topic, monkeypatch):
    p = good_plan(src)
    p["solo"]["q"] = p["steps"][1]["check"]["q"].upper()
    r, _ = go(fctx, topic, monkeypatch, p, p)
    assert r["status"] == "rejected" and "different" in r["why"]
    p = good_plan(src)
    p["solo"]["q"] = p["guided"]["q"]
    r, _ = go(fctx, topic, monkeypatch, p, p)
    assert r["status"] == "rejected"


def test_every_key_goes_through_keys_check(fctx, src, topic, monkeypatch):
    seen = []
    real = keys.check

    async def spy(ctx, q, key, c):
        seen.append(q)
        return await real(ctx, q, key, c)
    monkeypatch.setattr(keys, "check", spy)
    go(fctx, topic, monkeypatch, good_plan(src, 4))
    assert len(seen) == 4 + 2                                                         # four checks, guided, solo


def test_term_key_must_sit_inside_the_quote_not_just_the_chunk(fctx, src):
    """The E6 known limit: a wrong term that is anywhere in the chunk got through. Chosen fix: for a plan the term has to be in the
    cited quote (6 to 25 words), which is free and code only. 'the run' ends the chunk but is not in the quote."""
    c = cite(src)
    ok = lambda w: run(plan.check_key(fctx, "Which half is faster?", term_key(w), c))["ok"]
    assert ok("faster") and ok("negative split")
    assert not ok("the run")
    assert not ok("first half of the run")
    r = run(plan.check_key(fctx, "q", term_key("the run"), c))
    assert "quote" in r["why"]


def test_other_kinds_are_not_touched_by_the_tight_rule(fctx, src, monkeypatch):
    seen = []

    async def fake(ctx, q, key, c):
        seen.append(key)
        return {"ok": True, "why": "", "kind": "number", "cost": 0.001}
    monkeypatch.setattr(keys, "check", fake)
    r = run(plan.check_key(fctx, "q", {"expr": "2", "unit": ""}, cite(src)))
    assert r["ok"] and seen


# ---- shape ---------------------------------------------------------------------------------------------------------
def test_too_few_steps_rejects_too_many_are_cut(fctx, src, topic, monkeypatch):
    r, _ = go(fctx, topic, monkeypatch, good_plan(src, 2), good_plan(src, 2))
    assert r["status"] == "rejected" and "steps" in r["why"]
    r, _ = go(fctx, topic, monkeypatch, good_plan(src, 8))
    assert r["status"] == "usable" and len(plan.get(fctx, topic)["plan"]["steps"]) == 6


def test_every_step_needs_all_three_ways(fctx, src, topic, monkeypatch):
    p = good_plan(src)
    p["steps"][1]["explain"] = p["steps"][1]["explain"][:2]
    r, _ = go(fctx, topic, monkeypatch, p, p)
    assert r["status"] == "rejected" and "ways" in r["why"]
    p = good_plan(src)
    p["steps"][1]["explain"][2]["way"] = "plain"                                       # two plains is not three ways
    r, _ = go(fctx, topic, monkeypatch, p, p)
    assert r["status"] == "rejected"


def test_say_is_cut_to_forty_words(fctx, src, topic, monkeypatch):
    p = good_plan(src)
    long = "This is a sentence of ordinary length here. " + " ".join(["word"] * 60) + "."
    p["steps"][0]["explain"][0]["say"] = long
    go(fctx, topic, monkeypatch, p)
    said = plan.get(fctx, topic)["plan"]["steps"][0]["explain"][0]["say"]
    assert 0 < len(said.split()) <= 40 and said.startswith("This is a sentence")


def test_empty_say_rejects(fctx, src, topic, monkeypatch):
    p = good_plan(src)
    p["steps"][0]["explain"][1]["say"] = "  "
    r, _ = go(fctx, topic, monkeypatch, p, p)
    assert r["status"] == "rejected"


# ---- boards --------------------------------------------------------------------------------------------------------
GOOD_BOARD = {"title": "t", "steps": [
    {"say": "Two halves.", "ask": False, "do": [{"t": "axes", "x": [0, 10], "y": [0, 6], "bare": True}, {"t": "text", "x": 2, "y": 3, "s": "first half"}]},
    {"say": "Second faster.", "ask": False, "do": [{"t": "text", "x": 6, "y": 3, "s": "second half"}]}]}


def test_board_that_draws_is_kept_one_that_does_not_is_dropped(fctx, src, topic, monkeypatch):
    p = good_plan(src)
    p["steps"][0]["explain"][1]["board"] = copy.deepcopy(GOOD_BOARD)
    p["steps"][1]["explain"][1]["board"] = {"title": "x", "steps": "not a list"}
    r, _ = go(fctx, topic, monkeypatch, p)
    assert r["status"] == "usable"
    st = plan.get(fctx, topic)["plan"]["steps"]
    b = st[0]["explain"][1]["board"]
    assert b and b["scene"]["steps"] and len(b["asks"]) == len(b["scene"]["steps"])
    assert st[1]["explain"][1]["board"] is None and st[1]["explain"][1]["say"]        # the step keeps its words


def test_board_is_cut_to_four_steps(fctx, src, topic, monkeypatch):
    p = good_plan(src)
    big = copy.deepcopy(GOOD_BOARD)
    big["steps"] = big["steps"] * 4
    p["steps"][0]["explain"][1]["board"] = big
    go(fctx, topic, monkeypatch, p)
    b = plan.get(fctx, topic)["plan"]["steps"][0]["explain"][1]["board"]
    assert len(b["scene"]["steps"]) <= 4


# ---- the model fails -----------------------------------------------------------------------------------------------
def test_unreadable_reply_is_rejected_after_one_retry(fctx, src, topic, monkeypatch):
    r, m = go(fctx, topic, monkeypatch, "I cannot do that", "still not json")
    assert r["status"] == "rejected" and len(m.calls) == 2


def test_a_cap_stops_it_without_raising(fctx, src, topic, monkeypatch):
    r, m = go(fctx, topic, monkeypatch, MistyError("daily cap"))
    assert r["status"] == "rejected" and "cap" in r["why"].lower() and len(m.calls) == 1   # no second try on a cap


def test_unknown_topic(fctx, monkeypatch):
    with pytest.raises(Exception):
        run(plan.make(fctx, "tt_nope"))


# ---- the job -------------------------------------------------------------------------------------------------------
def test_job_is_registered_and_live():
    assert "teach_plan" in pipeline.REGISTRY
    src_ = open(__file__.replace("tests/engine/test_teach_plan.py", "engine/src/misty_engine/main.py")).read()
    assert '"teach_plan"' in src_ and '"teach_plan": 1' in src_ and 'build_kinds = ["discover", "prep", "compile_batch"]' in src_
    live = src_.split("live_kinds = [")[1].split("]")[0]
    assert "teach_plan" in live


def test_queue_plan_once_and_the_handler_makes_it(fctx, src, topic, monkeypatch):
    r = plan.queue_plan(fctx, topic)
    assert r["action"] == "queued" and plan.queue_plan(fctx, topic)["action"] == "already"
    assert len(fctx.con.execute("select * from jobs where kind='teach_plan'").fetchall()) == 1
    monkeypatch.setattr(ask, "think", Model(good_plan(src)))
    job = fctx.queue.claim(["teach_plan"], 1)[0]
    run(pipeline.REGISTRY["teach_plan"](job, fctx))
    assert plan.get(fctx, topic)["status"] == "usable"
    assert plan.queue_plan(fctx, topic)["action"] == "have"                           # a usable plan is not made twice
    fctx.con.execute("update jobs set status='done'")                                  # the worker marks it done after the handler returns
    assert plan.queue_plan(fctx, topic, force=True)["action"] == "queued"


def test_queue_ready_queues_only_topics_without_a_plan(fctx, src, topic, monkeypatch):
    s = fctx.con.execute("select subject_id from teach_topics where id=?", (topic,)).fetchone()[0]
    t2 = tm.add_topic(fctx, s, "fuelling", "Fuelling", {"source_id": src, "chunk_no": 2, "quote": QUOTES["c"]})["id"]
    n = plan.queue_ready(fctx, s)
    assert n["queued"] == 2
    assert plan.queue_ready(fctx, s)["queued"] == 0                                    # already on the queue
    fctx.con.execute("delete from jobs")
    fctx.con.execute("INSERT INTO teach_plans(id, topic_id, version, plan_json, model, cost, status, made_at) VALUES ('tp_x',?,1,'{}','haiku',0,'usable',1)", (t2,))
    assert plan.queue_ready(fctx, s)["queued"] == 1


def test_plan_cost_is_under_the_card_cap_on_a_normal_plan(fctx, src, topic, monkeypatch):
    r, _ = go(fctx, topic, monkeypatch, good_plan(src))
    assert r["cost"] < plan.MAX_PLAN_USD
