"""E6: the answer checker. Fake model replies only, no paid call. A made-up stranger's subject."""
import asyncio
import json

import pytest

from misty_engine.teach import keys, sources


def run(coro):
    return asyncio.run(coro)


PASSAGE = ("A trolley of mass 4.0 kg is pushed with a force of 10 N. Newton's second law says F = ma. "
           "Heat is measured in joules. The catalyst speeds up a reaction without being used up.")


@pytest.fixture
def cite(fctx):
    r = sources._keep(fctx, "page", "https://x.test/p", "p", PASSAGE)
    return {"source_id": r["id"], "chunk_no": 0}


def reply(fake_or, **kw):
    fake_or.chat_returns(json.dumps(kw))


def last_user(fake_or):
    return fake_or.chat_bodies[-1]["messages"][-1]["content"]


NUM = {"expr": "10/4", "unit": "m/s^2", "sig_figs": 2}
Q = "What is the acceleration of the trolley?"


# ---- number ----
def test_number_agrees(fctx, fake_or, cite):
    reply(fake_or, expr="10/4.0", unit="m s^-2")
    r = run(keys.check(fctx, Q, NUM, cite))
    assert r["ok"] and r["kind"] == "number"


@pytest.mark.parametrize("expr,unit,why", [("25", "m/s^2", "disagrees"), ("-2.5", "m/s^2", "disagrees"), ("2.5", "m/s", "unit"),
                                           ("2.5", "", "unit"), ("hello", "m/s^2", "no number")])
def test_number_disagreement_is_thrown_away(fctx, fake_or, cite, expr, unit, why):
    reply(fake_or, expr=expr, unit=unit)
    r = run(keys.check(fctx, Q, NUM, cite))
    assert not r["ok"] and why in r["why"]


def test_second_solve_never_sees_the_key(fctx, fake_or, cite):
    reply(fake_or, expr="2.5", unit="m/s^2")
    run(keys.check(fctx, Q, {"expr": "10/4", "unit": "m/s^2", "sig_figs": 2}, cite))
    body = json.dumps(fake_or.chat_bodies[0])
    assert "10/4" not in body and "sig_figs" not in body and "answer" not in fake_or.chat_bodies[0]["messages"][-1]["content"].lower().split("<question>")[0]
    assert fake_or.chat_bodies[0]["reasoning"]["enabled"] is True and fake_or.chat_bodies[0]["temperature"] == 0.1


def test_bad_number_key_costs_nothing(fctx, fake_or, cite):
    for k in ({"expr": "1/0", "unit": ""}, {"expr": "__import__('os')", "unit": ""}, {"expr": "2", "unit": "", "sig_figs": 0}):
        r = run(keys.check(fctx, Q, k, cite))
        assert not r["ok"]
    assert fake_or.chat_bodies == []


# ---- choice ----
CH = {"options": ["1.0 m/s^2", "2.5 m/s^2", "4.0 m/s^2", "40 m/s^2"], "answer": "2.5 m/s^2"}


def test_choice_agrees_and_disagrees(fctx, fake_or, cite):
    reply(fake_or, option="B")
    assert run(keys.check(fctx, Q, CH, cite))["ok"]
    reply(fake_or, option="C")
    r = run(keys.check(fctx, Q, CH, cite))
    assert not r["ok"] and "another option" in r["why"]
    reply(fake_or, option="Z")
    assert not run(keys.check(fctx, Q, CH, cite))["ok"]


def test_choice_key_must_be_well_formed(fctx, fake_or, cite):
    for k in ({"options": ["a"], "answer": "a"}, {"options": ["a", "A"], "answer": "a"}, {"options": ["a", "b"], "answer": "c"}):
        assert not run(keys.check(fctx, Q, k, cite))["ok"]
    assert fake_or.chat_bodies == []


def test_choice_answer_by_index_and_second_solve_does_not_see_answer(fctx, fake_or, cite):
    reply(fake_or, option="B")
    assert run(keys.check(fctx, Q, {"options": CH["options"], "answer": 1}, cite))["ok"]
    assert '"answer"' not in json.dumps(fake_or.chat_bodies[0]) and "2.5 m/s^2" in last_user(fake_or)


# ---- term (pure code) ----
def test_term_must_be_in_the_passage(fctx, fake_or, cite):
    assert run(keys.check(fctx, "What speeds a reaction up?", {"accept": ["catalyst"]}, cite))["ok"]
    r = run(keys.check(fctx, "What speeds a reaction up?", {"accept": ["catalyst", "enzyme"]}, cite))
    assert not r["ok"] and "enzyme" in r["why"]
    assert fake_or.chat_bodies == []


# ---- points ----
PT = {"points": [{"text": "F equals m times a", "quote": "Newton's second law says F = ma"}]}


def test_points_yes_passes_and_unsure_fails(fctx, fake_or, cite):
    fake_or.chat_returns("yes")
    assert run(keys.check(fctx, "State Newton's second law.", PT, cite))["ok"]
    for bad in ("unsure", "no", "Yes, but it is wrong", "maybe"):
        fake_or.chat_returns(bad)
        assert not run(keys.check(fctx, "State Newton's second law.", PT, cite))["ok"]      # only a bare yes counts
    fake_or.chat_returns("Yes.")
    assert run(keys.check(fctx, "State Newton's second law.", PT, cite))["ok"]


def test_points_with_a_made_up_quote_never_reach_the_model(fctx, fake_or, cite):
    k = {"points": [{"text": "heat is in watts", "quote": "Heat is measured in watts"}]}
    r = run(keys.check(fctx, "What measures heat?", k, cite))
    assert not r["ok"] and "quote" in r["why"] and fake_or.chat_bodies == []


def test_every_point_must_pass(fctx, fake_or, cite):
    k = {"points": PT["points"] + [{"text": "heat is in watts", "quote": "Heat is measured in joules"}]}
    fake_or.chat_returns("yes")
    fake_or.chat_returns("no")
    assert not run(keys.check(fctx, "q", k, cite))["ok"]


# ---- the rest ----
def test_missing_passage_unknown_key_and_dead_model(fctx, fake_or, cite):
    assert not run(keys.check(fctx, Q, NUM, {"source_id": "nope", "chunk_no": 0}))["ok"]
    assert not run(keys.check(fctx, Q, {"foo": 1}, cite))["ok"]
    fake_or.fail("/api/v1/chat/completions", 500, times=6)
    r = run(keys.check(fctx, Q, NUM, cite))
    assert not r["ok"]


def test_every_check_is_logged(fctx, fake_or, cite):
    reply(fake_or, expr="2.5", unit="m/s^2")
    run(keys.check(fctx, Q, NUM, cite))
    run(keys.check(fctx, Q, {"accept": ["enzyme"]}, cite))
    rows = fctx.con.execute("SELECT kind, ok FROM teach_keylog ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("number", 1), ("term", 0)]


# ---- grading ----
G_NUM = {"expr": "0.5*0.5*12^2", "unit": "J", "sig_figs": 2}      # 36 J


@pytest.mark.parametrize("his,verdict", [("36 J", "right"), ("36 joules", "right"), ("It is 36 J.", "right"), ("36", "partly"),
                                         ("35 J", "partly"), ("40 J", "wrong"), ("36 N", "wrong"), ("0.036 kJ", "wrong"),
                                         ("360 J", "wrong"), ("-36 J", "wrong"), ("no idea", "wrong"), ("", "wrong"), ("0.5*0.5*144 J", "right")])
def test_grade_number(fctx, his, verdict):
    assert run(keys.grade(fctx, "q", G_NUM, his))["verdict"] == verdict


def test_grade_number_unitless_key(fctx):
    k = {"expr": "2*3+7", "unit": ""}
    assert run(keys.grade(fctx, "q", k, "13"))["verdict"] == "right"
    assert run(keys.grade(fctx, "q", k, "13 metres"))["verdict"] == "wrong"
    assert run(keys.grade(fctx, "q", {"expr": "3.2e5", "unit": "m/s"}, "3.2 x 10^5 m/s"))["verdict"] == "right"


@pytest.mark.parametrize("his,verdict", [("B", "right"), ("b)", "right"), ("option B", "right"), ("2.5 m/s^2", "right"), ("C", "wrong"),
                                         ("I think 4.0 m/s^2", "wrong"), ("hmm", "wrong")])
def test_grade_choice(fctx, his, verdict):
    assert run(keys.grade(fctx, "q", CH, his))["verdict"] == verdict


def test_grade_term(fctx):
    k = {"accept": ["catalyst"]}
    assert run(keys.grade(fctx, "q", k, "a Catalyst"))["verdict"] == "right"
    assert run(keys.grade(fctx, "q", k, "an enzyme"))["verdict"] == "wrong"
    assert run(keys.grade(fctx, "q", k, "catalysts"))["verdict"] == "wrong"


def test_grade_points_all_some_none(fctx, fake_or):
    k = {"points": [{"text": "a", "quote": "x x x"}, {"text": "b", "quote": "y y y"}]}
    for covered, verdict in (([0, 1], "right"), ([1], "partly"), ([], "wrong"), ([7], "wrong")):
        reply(fake_or, covered=covered)
        assert run(keys.grade(fctx, "q", k, "whatever"))["verdict"] == verdict
    fake_or.chat_returns("garbage")
    assert run(keys.grade(fctx, "q", k, "whatever"))["verdict"] == "wrong"
