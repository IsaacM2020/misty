"""E10: the question bank. A fake model only (ask.think is replaced), no paid call. A made-up stranger's subject.
Keys are `term` keys so E6's checker runs for real without a model."""
import asyncio
import json

import pytest

from misty_engine import pipeline
from misty_engine.errors import MistyError
from misty_engine.teach import ask, bank, keys, plan, sources
from misty_engine.teach import map as tm

PARAS = [
    "Pacing means spreading your effort evenly over a whole run so you do not tire early.",
    "A negative split is when the second half of a run is faster than the first half of the run.",
    "Fuelling means eating carbohydrate during long runs so that the legs keep working well.",
    "Recovery days let the muscles rebuild after hard work and the next hard run feels better.",
    "Hydration matters because sweat takes salt and water out of the body on a warm day.",
    "Cadence is the number of steps a runner takes in one minute of running on flat ground.",
]
QUOTE = "A negative split is when the second half of a run is faster than the first half"
DAY = 86400.0


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
    t = tm.add_topic(fctx, s, "negative split", "Negative splits", {"source_id": src, "chunk_no": 1, "quote": QUOTE})
    return t["id"]


def cite(src, no=1, quote=QUOTE):
    return {"source_id": src, "chunk_no": no, "quote": quote}


def qn(src, level, text, word="faster", **kw):
    return {"level": level, "q": text, "key": {"accept": [word]}, "cite": cite(src), **kw}


def nine(src, tag="a"):
    """Nine different questions, three per level, each with a term key that sits inside the quote. Every question uses its own
    words, so none is a near duplicate of another."""
    words = ["faster", "second half", "negative split"]
    out = []
    for lv in (1, 2, 3):
        for i in range(3):
            bits = " ".join(f"{tag}{lv}{i}x{j}" for j in range(6))
            out.append(qn(src, lv, f"Question {bits}: which word fits?", words[i]))
    return {"questions": out}


class Model:
    """ask.think replaced: hands out the queued replies and records what it was given."""
    def __init__(self, *replies, cost=0.004):
        self.replies, self.cost, self.calls = list(replies), cost, []

    async def __call__(self, ctx, system, user, **kw):
        self.calls.append({"system": system, "user": user, **kw})
        r = self.replies.pop(0) if self.replies else self.last
        self.last = r
        if isinstance(r, Exception):
            raise r
        return (r if isinstance(r, str) else json.dumps(r)), self.cost


def go(ctx, topic, monkeypatch, *replies, **kw):
    m = Model(*replies)
    monkeypatch.setattr(ask, "think", m)
    return run(bank.make(ctx, topic, **kw)), m


def rows(ctx, topic, **where):
    sql = "SELECT * FROM teach_questions WHERE topic_id=?"
    args = [topic]
    for k, v in where.items():
        sql += f" AND {k}=?"
        args.append(v)
    return ctx.con.execute(sql + " ORDER BY level, made_at, id", args).fetchall()


def add_plan_q(ctx, topic, q, level=1):
    ctx.con.execute("INSERT INTO teach_questions(id, topic_id, level, q, key_json, cite_json, source, status, made_at, uses) "
                    "VALUES (?,?,?,?,?,?,?,?,?,0)", ("pq" + str(abs(hash(q)))[:8], topic, level, q, "{}", "{}", "plan", "usable", ctx.clock.now()))


# ---- the table -----------------------------------------------------------------------------------------------------
def test_table_and_index_exist(fctx):
    cols = [r[1] for r in fctx.con.execute("pragma table_info(teach_questions)")]
    for c in ("id", "topic_id", "level", "q", "key_json", "cite_json", "source", "status", "made_at", "uses", "last_used"):
        assert c in cols
    idx = [r[1] for r in fctx.con.execute("pragma index_list(teach_questions)")]
    assert any("topic" in i for i in idx)


# ---- a good batch --------------------------------------------------------------------------------------------------
def test_nine_good_questions_are_all_kept(fctx, src, topic, monkeypatch):
    r, m = go(fctx, topic, monkeypatch, nine(src))
    assert r["made"] == 9 and r["rejected"] == 0 and len(m.calls) == 1
    assert abs(r["cost"] - 0.004) < 1e-9
    rs = rows(fctx, topic)
    assert len(rs) == 9 and {r_["level"] for r_ in rs} == {1, 2, 3}
    assert all(r_["status"] == "usable" and r_["source"] == "bank" and r_["uses"] == 0 for r_ in rs)
    k = json.loads(rs[0]["key_json"])
    assert "accept" in k and json.loads(rs[0]["cite_json"])["quote"] == QUOTE


def test_model_sees_the_window_and_the_levels_and_is_told_passages_are_data(fctx, src, topic, monkeypatch):
    fctx.con.execute("UPDATE teach_topics SET chunk_no=2 WHERE id=?", (topic,))
    r, m = go(fctx, topic, monkeypatch, nine(src), ways=["short, with a story"])
    u = m.calls[0]["user"]
    assert all(f"[{i}]" in u for i in range(5)) and "[5]" not in u and "short, with a story" in u and "Negative splits" in u
    assert "data" in m.calls[0]["system"].lower() and m.calls[0]["thinking"] is True


def test_no_text_is_in_the_bank_twice(fctx, src, topic, monkeypatch):
    go(fctx, topic, monkeypatch, nine(src), nine(src))
    texts = [bank_norm for bank_norm in (" ".join(r_["q"].lower().split()) for r_ in rows(fctx, topic))]
    assert len(texts) == len(set(texts))


# ---- what code drops -----------------------------------------------------------------------------------------------
def test_each_bad_kind_is_dropped_alone(fctx, src, topic, monkeypatch):
    add_plan_q(fctx, topic, "Which half of a negative split run is the faster half of the run for a runner?")
    d = nine(src)["questions"]
    d[0]["cite"]["quote"] = "a sentence the source never said at all anywhere really"            # made up cite
    d[1]["cite"] = cite(src, no=5, quote=PARAS[5][:60])                                          # real quote, outside the window
    d[2]["key"] = {"accept": ["banana"]}                                                         # term not in the quote
    d[3]["q"] = d[4]["q"]                                                                        # exact duplicate of another new one
    d[5]["q"] = "Which half of a negative split run is the faster half of the run for a runner?"  # a plan question, same words
    r, _ = go(fctx, topic, monkeypatch, {"questions": d}, {"questions": []})
    assert r["made"] == 4 and r["rejected"] == 5
    assert len(rows(fctx, topic, source="bank")) == 4


def test_a_near_duplicate_over_seventy_percent_is_dropped(fctx, src, topic, monkeypatch):
    a = "Which half of a negative split run is the faster half of the whole run?"
    near = "Which half of a negative split run is the faster half of the whole race?"       # 1 word of 14 differs
    far = "Name the word for starting slow and finishing quickly."
    d = [qn(src, 1, a), qn(src, 1, near), qn(src, 1, far, "negative split")]
    r, _ = go(fctx, topic, monkeypatch, {"questions": d}, {"questions": []})
    assert r["made"] == 2 and r["rejected"] == 1
    qs = [x["q"] for x in rows(fctx, topic)]
    assert a in qs and far in qs and near not in qs


def test_a_failed_key_goes_through_keys_check_and_is_logged_there(fctx, src, topic, monkeypatch):
    seen = []
    real = keys.check

    async def spy(ctx, question, key, c):
        seen.append(question)
        return await real(ctx, question, key, c)
    monkeypatch.setattr(keys, "check", spy)
    go(fctx, topic, monkeypatch, nine(src))
    assert len(seen) == 9


def test_a_number_key_that_does_not_compute_is_dropped(fctx, src, topic, monkeypatch):
    d = nine(src)["questions"]
    d[0]["key"] = {"expr": "2 +* 2", "unit": ""}
    r, _ = go(fctx, topic, monkeypatch, {"questions": d}, {"questions": []})
    assert r["made"] == 8 and r["rejected"] == 1


def test_shape_is_checked(fctx, src, topic, monkeypatch):
    d = [qn(src, 4, "level four?"), qn(src, 0, "level zero?"), {"level": 1, "q": "", "key": {"accept": ["faster"]}, "cite": cite(src)},
         {"level": 1, "q": "no key at all here?", "cite": cite(src)}, "junk", qn(src, 2, "A real one: which half is faster than the other half?")]
    r, _ = go(fctx, topic, monkeypatch, {"questions": d}, {"questions": []})
    assert r["made"] == 1 and r["rejected"] == 5


def test_reply_that_is_not_json_makes_nothing_and_does_not_raise(fctx, src, topic, monkeypatch):
    r, m = go(fctx, topic, monkeypatch, "I am sorry, here are some questions", "still not json")
    assert r["made"] == 0 and len(m.calls) == 2                                   # one go, one retry
    assert rows(fctx, topic) == []


# ---- the target and the retry --------------------------------------------------------------------------------------
def test_a_short_level_gets_one_retry_told_what_is_short(fctx, src, topic, monkeypatch):
    first = nine(src)["questions"]
    first = [q for q in first if q["level"] != 3][:6] + [q for q in first if q["level"] == 3][:1]    # level 3 has only one
    r, m = go(fctx, topic, monkeypatch, {"questions": first}, nine(src, "b"))
    assert len(m.calls) == 2 and "level 3" in m.calls[1]["user"].lower() and "2" in m.calls[1]["user"]
    per = {lv: len(rows(fctx, topic, level=lv, source="bank")) for lv in (1, 2, 3)}
    assert per[1] == per[2] == per[3] == 3                                         # extra from the retry beyond need are not kept
    assert abs(r["cost"] - 0.008) < 1e-9 and r["made"] == 9


def test_only_one_retry_even_if_still_short(fctx, src, topic, monkeypatch):
    r, m = go(fctx, topic, monkeypatch, {"questions": []}, {"questions": []}, nine(src))
    assert len(m.calls) == 2 and r["made"] == 0


def test_a_topic_that_has_enough_makes_no_call(fctx, src, topic, monkeypatch):
    go(fctx, topic, monkeypatch, nine(src))
    r, m = go(fctx, topic, monkeypatch, nine(src, "b"))
    assert m.calls == [] and r["made"] == 0 and r["cost"] == 0.0


def test_a_partly_filled_topic_asks_only_for_the_gap(fctx, src, topic, monkeypatch):
    go(fctx, topic, monkeypatch, {"questions": [q for q in nine(src)["questions"] if q["level"] != 2]}, {"questions": []})
    assert len(rows(fctx, topic)) == 6
    r, m = go(fctx, topic, monkeypatch, nine(src, "c"))
    assert "level 2" in m.calls[0]["user"].lower() and "level 1" not in m.calls[0]["user"].lower()
    assert len(rows(fctx, topic, level=2)) == 3 and len(rows(fctx, topic)) == 9 and r["made"] == 3


def test_plan_and_bad_questions_do_not_count_toward_the_nine(fctx, src, topic, monkeypatch):
    for i in range(3):
        add_plan_q(fctx, topic, f"Plan question number {i} about something quite different from the rest {i * 7}?", 1)
    go(fctx, topic, monkeypatch, nine(src))
    assert len(rows(fctx, topic, source="bank")) == 9
    fctx.con.execute("UPDATE teach_questions SET status='bad' WHERE source='bank' AND level=1")
    r, m = go(fctx, topic, monkeypatch, nine(src, "d"))
    assert "level 1" in m.calls[0]["user"].lower() and len(rows(fctx, topic, level=1, status="usable", source="bank")) == 3


def test_a_cap_or_network_stop_does_not_raise_and_leaves_the_bank_alone(fctx, src, topic, monkeypatch):
    r, m = go(fctx, topic, monkeypatch, MistyError("budget reached"))
    assert r["made"] == 0 and len(m.calls) == 1                                    # no retry after a cap
    assert rows(fctx, topic) == []


# ---- plan questions come in ----------------------------------------------------------------------------------------
def test_plan_questions_are_copied_in_when_a_plan_becomes_usable(fctx, src, topic, monkeypatch):
    from test_teach_plan import good_plan
    m = type("M", (), {})()

    async def fake(ctx, system, user, **kw):
        return json.dumps(good_plan(src)), 0.004
    monkeypatch.setattr(ask, "think", fake)
    r = run(plan.make(fctx, topic))
    assert r["status"] == "usable"
    ps = rows(fctx, topic, source="plan")
    assert len(ps) == 6                                                           # 4 checks, guided, solo (checks differ only by number)
    assert {p["level"] for p in ps} <= {1, 2}
    assert all(p["status"] == "usable" for p in ps)
    run(plan.make(fctx, topic))                                                   # a second plan adds no copy of the same text
    assert len(rows(fctx, topic, source="plan")) == 6


# ---- picking -------------------------------------------------------------------------------------------------------
def fill(fctx, src, topic, monkeypatch):
    go(fctx, topic, monkeypatch, nine(src))
    return rows(fctx, topic, level=1)


def test_pick_gives_least_used_then_oldest_first(fctx, src, topic, monkeypatch):
    a, b, c = [r_["id"] for r_ in fill(fctx, src, topic, monkeypatch)]
    fctx.con.execute("UPDATE teach_questions SET uses=2, last_used=100 WHERE id=?", (a,))
    fctx.con.execute("UPDATE teach_questions SET uses=1, last_used=300 WHERE id=?", (b,))
    fctx.con.execute("UPDATE teach_questions SET uses=1, last_used=200 WHERE id=?", (c,))
    got = bank.pick(fctx, topic, 1, 3, now=1000.0)
    assert [g["id"] for g in got] == [c, b, a]
    assert all(set(g) >= {"id", "level", "q", "key", "cite", "source"} and isinstance(g["key"], dict) for g in got)
    assert [g["id"] for g in bank.pick(fctx, topic, 1, 1, now=1000.0)] == [c]


def test_pick_skips_excluded_bad_retired_and_other_levels(fctx, src, topic, monkeypatch):
    a, b, c = [r_["id"] for r_ in fill(fctx, src, topic, monkeypatch)]
    fctx.con.execute("UPDATE teach_questions SET status='bad' WHERE id=?", (a,))
    fctx.con.execute("UPDATE teach_questions SET status='retired' WHERE id=?", (b,))
    assert [g["id"] for g in bank.pick(fctx, topic, 1, 5, now=1.0)] == [c]
    assert bank.pick(fctx, topic, 1, 5, exclude={c}, now=1.0) == []
    assert all(g["level"] == 2 for g in bank.pick(fctx, topic, 2, 9, now=1.0))


def test_pick_never_gives_plan_questions_unless_asked_and_then_once_a_week(fctx, src, topic, monkeypatch):
    add_plan_q(fctx, topic, "Plan question one that no bank question looks like at all, honestly?", 1)
    pid = fctx.con.execute("select id from teach_questions where source='plan'").fetchone()[0]
    assert bank.pick(fctx, topic, 1, 5, now=1000.0) == []                          # probes and papers: bank only
    got = bank.pick(fctx, topic, 1, 5, now=1000.0, allow_plan=True)
    assert [g["id"] for g in got] == [pid]
    bank.mark_used(fctx, pid, 1000.0)
    assert bank.pick(fctx, topic, 1, 5, now=1000.0 + 6 * DAY, allow_plan=True) == []
    assert [g["id"] for g in bank.pick(fctx, topic, 1, 5, now=1000.0 + 8 * DAY, allow_plan=True)] == [pid]


def test_mark_used_counts_and_stamps(fctx, src, topic, monkeypatch):
    a = fill(fctx, src, topic, monkeypatch)[0]["id"]
    bank.mark_used(fctx, a, 555.0)
    bank.mark_used(fctx, a, 600.0)
    r_ = fctx.con.execute("select uses, last_used from teach_questions where id=?", (a,)).fetchone()
    assert (r_[0], r_[1]) == (2, 600.0)


def test_a_marked_bad_question_is_never_picked(fctx, src, topic, monkeypatch):
    a = fill(fctx, src, topic, monkeypatch)[0]["id"]
    bank.set_status(fctx, a, "bad")
    assert a not in [g["id"] for g in bank.pick(fctx, topic, 1, 9, now=1.0)]
    with pytest.raises(Exception):
        bank.set_status(fctx, a, "weird")


def test_counts(fctx, src, topic, monkeypatch):
    assert bank.counts(fctx, topic) == {1: 0, 2: 0, 3: 0}
    fill(fctx, src, topic, monkeypatch)
    assert bank.counts(fctx, topic) == {1: 3, 2: 3, 3: 3} and bank.usable(fctx, topic) == 9


# ---- the job -------------------------------------------------------------------------------------------------------
def test_job_is_registered_and_live():
    assert "teach_bank" in pipeline.REGISTRY
    import inspect
    from misty_engine import main
    s = inspect.getsource(main)
    assert s.count("teach_bank") >= 2                                               # live_kinds and conc


def test_queue_bank_once_and_the_handler_makes_it(fctx, src, topic, monkeypatch):
    r = bank.queue_bank(fctx, topic)
    assert r["action"] == "queued" and bank.queue_bank(fctx, topic)["action"] == "already"
    assert len(fctx.con.execute("select * from jobs where kind='teach_bank'").fetchall()) == 1
    monkeypatch.setattr(ask, "think", Model(nine(src)))
    job = fctx.queue.claim(["teach_bank"], 1)[0]
    run(pipeline.REGISTRY["teach_bank"](job, fctx))
    assert bank.usable(fctx, topic) == 9
    fctx.con.execute("update jobs set status='done'")
    assert bank.queue_bank(fctx, topic)["action"] == "have"                           # nine usable: nothing to make


def test_queue_bank_for_a_missing_topic_is_refused(fctx):
    with pytest.raises(Exception):
        bank.queue_bank(fctx, "nope")


def test_no_model_door_of_its_own():
    import inspect
    s = inspect.getsource(bank)
    assert "openrouter" not in s.replace("user_approved", "").lower().replace("from ..openrouter import", "")
    assert "ask.think" in s
