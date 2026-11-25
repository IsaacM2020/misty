"""E7: the pure lesson machine. step(state, event) -> (state, actions). No db, no model. Every arrow of the card's path."""
import copy

import pytest

from misty_engine.teach import lesson as L


def new(n=4, checks=None):
    return L.new_state(n, checks if checks is not None else [True] * n)


def do(actions, kind):
    return [a for a in actions if a["do"] == kind]


def kinds(actions):
    return [a["do"] for a in actions]


def ans(v, needs_open=False):
    return {"e": "answer", "verdict": v, "needs_open": needs_open}


def go(state, *events):
    acts = []
    for e in events:
        state, a = L.step(state, e)
        acts += a
    return state, acts


# ---- start ---------------------------------------------------------------------------------------------------------
def test_start_explains_step_zero_plain_then_asks():
    s, a = L.step(new(), {"e": "start"})
    assert (s["step"], s["phase"], s["way"], s["status"]) == (0, "check", 0, "live")
    assert kinds(a) == ["explain", "ask"] and a[0]["way"] == 0 and a[1]["phase"] == "check"


def test_explain_only_step_moves_straight_on():
    s, a = L.step(new(4, [False, True, True, True]), {"e": "start"})
    assert s["step"] == 1 and kinds(a) == ["explain", "explain", "ask"] and [x["step"] for x in do(a, "explain")] == [0, 1]


def test_step_does_not_mutate_its_input():
    s0 = new()
    snap = copy.deepcopy(s0)
    L.step(s0, {"e": "start"})
    assert s0 == snap


# ---- check ----------------------------------------------------------------------------------------------------------
def test_right_writes_right_helped_and_goes_to_next_step():
    s, _ = L.step(new(), {"e": "start"})
    s, a = L.step(s, ans("right"))
    assert s["step"] == 1 and s["way"] == 0 and s["misses"] == 0
    assert [(e["kind"], e["step"]) for e in do(a, "evidence")] == [("right_helped", 0)]
    assert kinds(a)[-2:] == ["explain", "ask"]


@pytest.mark.parametrize("v", ["wrong", "partly"])
def test_a_miss_goes_back_to_explain_with_the_next_way(v):
    s, _ = L.step(new(), {"e": "start"})
    s, a = L.step(s, ans(v))
    assert s["step"] == 0 and s["way"] == 1 and s["misses"] == 1 and s["phase"] == "check"
    assert [x["way"] for x in do(a, "explain")] == [1] and do(a, "ask")
    s, a = L.step(s, ans(v))
    assert s["way"] == 2 and s["misses"] == 2 and [x["way"] for x in do(a, "explain")] == [2]


def test_wrong_evidence_is_written_once_per_step_however_many_misses():
    s, a1 = go(*[L.step(new(), {"e": "start"})[0]], ans("wrong"))
    s, a2 = go(s, ans("wrong"))
    assert len(do(a1, "evidence")) == 1 and do(a2, "evidence") == []
    assert len(do(a1, "slip")) == 1 and len(do(a2, "slip")) == 1          # every wrong answer is in the mistakes book


def test_right_after_a_miss_writes_wrong_once_and_right_helped_once():
    s, _ = L.step(new(), {"e": "start"})
    s, a = go(s, ans("wrong"), ans("right"))
    assert [e["kind"] for e in do(a, "evidence")] == ["wrong", "right_helped"]


def test_third_miss_without_an_open_need_shows_the_answer_and_moves_on():
    s, _ = L.step(new(), {"e": "start"})
    s, a = go(s, ans("wrong"), ans("wrong"), ans("wrong"))
    assert s["step"] == 1 and s["way"] == 0 and s["status"] == "live"
    r = do(a, "reveal")
    assert len(r) == 1 and r[0]["step"] == 0 and kinds(a)[-2:] == ["explain", "ask"]
    assert [e["kind"] for e in do(a, "evidence")] == ["wrong"]            # still one wrong for the step


def test_third_miss_with_an_open_need_pauses_and_offers_it():
    s, _ = L.step(new(), {"e": "start"})
    s, a = go(s, ans("wrong"), ans("wrong"), ans("wrong", needs_open=True))
    assert s["status"] == "paused" and s["offer"] is True and s["step"] == 0
    assert do(a, "offer_need") and not do(a, "reveal")


def test_offer_declined_shows_answer_and_carries_on():
    s, _ = L.step(new(), {"e": "start"})
    s, _ = go(s, ans("wrong"), ans("wrong"), ans("wrong", True))
    s, a = L.step(s, {"e": "need_no"})
    assert s["status"] == "live" and s["offer"] is False and s["step"] == 1 and do(a, "reveal")


def test_offer_accepted_leaves_the_lesson_paused_where_it_was():
    s, _ = L.step(new(), {"e": "start"})
    s, _ = go(s, ans("wrong"), ans("wrong"), ans("wrong", True))
    s2, a = L.step(s, {"e": "need_yes"})
    assert s2["status"] == "paused" and s2["step"] == 0 and do(a, "paused")


def test_stuck_gives_the_next_hint_and_stays():
    s, _ = L.step(new(), {"e": "start"})
    s, a = L.step(s, {"e": "stuck", "hints_avail": 3})
    assert s["step"] == 0 and s["way"] == 0 and s["misses"] == 0 and s["hints"] == 1
    assert [h["n"] for h in do(a, "hint")] == [0] and do(a, "ask")
    s, a = go(s, {"e": "stuck", "hints_avail": 3})
    assert [h["n"] for h in do(a, "hint")] == [1] and s["hints"] == 2


def test_stuck_with_no_hints_left_shows_the_answer():
    s, _ = L.step(new(), {"e": "start"})
    s, a = go(s, {"e": "stuck", "hints_avail": 1}, {"e": "stuck", "hints_avail": 1})
    assert do(a, "reveal") and s["step"] == 1


def test_show_me_in_check_shows_and_moves_on_with_no_evidence():
    s, _ = L.step(new(), {"e": "start"})
    s, a = L.step(s, {"e": "show_me"})
    assert s["step"] == 1 and do(a, "reveal") and not do(a, "evidence")


def test_other_changes_nothing():
    s, _ = L.step(new(), {"e": "start"})
    s2, a = L.step(s, {"e": "other"})
    assert s2 == s and kinds(a) == ["side"]


# ---- after the last step -------------------------------------------------------------------------------------------
def at_guided():
    s, _ = L.step(new(3), {"e": "start"})
    s, a = go(s, ans("right"), ans("right"), ans("right"))
    return s, a


def test_last_check_leads_to_guided():
    s, a = at_guided()
    assert s["phase"] == "guided" and s["step"] == 3 and do(a, "ask")[-1]["phase"] == "guided"


def test_guided_right_writes_right_helped_then_solo():
    s, _ = at_guided()
    s, a = L.step(s, ans("right"))
    assert s["phase"] == "solo" and [(e["kind"], e["phase"]) for e in do(a, "evidence")] == [("right_helped", "guided")]
    assert do(a, "ask")[-1]["phase"] == "solo"


def test_guided_miss_gives_a_hint_and_three_misses_reveal_and_move_on():
    s, _ = at_guided()
    s, a = L.step(s, ans("wrong"))
    assert s["phase"] == "guided" and [h["n"] for h in do(a, "hint")] == [0] and do(a, "slip") and not do(a, "evidence")
    s, a = go(s, ans("wrong"), ans("wrong"))
    assert s["phase"] == "solo" and do(a, "reveal")


def test_solo_right_alone_then_recap_taught_done():
    s, _ = at_guided()
    s, _ = L.step(s, ans("right"))
    s, a = L.step(s, ans("right"))
    assert [e["kind"] for e in do(a, "evidence")] == ["right_alone", "taught"]
    assert do(a, "recap") and do(a, "finished") and (s["phase"], s["status"]) == ("done", "done")


@pytest.mark.parametrize("v", ["wrong", "partly"])
def test_solo_miss_is_wrong_shows_answer_no_second_explanation(v):
    s, _ = at_guided()
    s, _ = L.step(s, ans("right"))
    s, a = L.step(s, ans(v))
    assert [e["kind"] for e in do(a, "evidence")] == ["wrong", "taught"] and do(a, "reveal") and not do(a, "explain") and s["status"] == "done"
    assert do(a, "slip")


def test_solo_show_me_counts_as_wrong():
    s, _ = at_guided()
    s, _ = L.step(s, ans("right"))
    s, a = L.step(s, {"e": "show_me"})
    assert [e["kind"] for e in do(a, "evidence")] == ["wrong", "taught"] and do(a, "reveal")


def test_solo_stuck_gets_no_hint():
    s, _ = at_guided()
    s, _ = L.step(s, ans("right"))
    s2, a = L.step(s, {"e": "stuck", "hints_avail": 3})
    assert s2 == s and do(a, "no_hints") and not do(a, "hint")


def test_guided_show_me_moves_to_solo_without_evidence():
    s, _ = at_guided()
    s, a = L.step(s, {"e": "show_me"})
    assert s["phase"] == "solo" and do(a, "reveal") and not do(a, "evidence")


# ---- again, easier, harder, stop, resume ---------------------------------------------------------------------------
def test_again_explains_the_same_way_and_asks_again_without_a_miss():
    s, _ = L.step(new(), {"e": "start"})
    s, _ = L.step(s, ans("wrong"))                       # now on way 1
    s2, a = L.step(s, {"e": "again"})
    assert s2["way"] == 1 and s2["misses"] == s["misses"] and [x["way"] for x in do(a, "explain")] == [1] and do(a, "ask")


def test_easier_is_the_next_way_without_counting_a_miss_and_stops_at_the_last_way():
    s, _ = L.step(new(), {"e": "start"})
    s, a = L.step(s, {"e": "easier"})
    assert s["way"] == 1 and s["misses"] == 0 and [x["way"] for x in do(a, "explain")] == [1]
    s, _ = L.step(s, {"e": "easier"})
    s, a = L.step(s, {"e": "easier"})
    assert s["way"] == 2 and [x["way"] for x in do(a, "explain")] == [2]


def test_harder_skips_to_guided_and_then_to_solo():
    s, _ = L.step(new(), {"e": "start"})
    s, a = L.step(s, {"e": "harder"})
    assert s["phase"] == "guided" and do(a, "ask")[-1]["phase"] == "guided" and not do(a, "evidence")
    s, a = L.step(s, {"e": "harder"})
    assert s["phase"] == "solo"
    s, a = L.step(s, {"e": "harder"})
    assert s["phase"] == "solo"


def test_stop_and_resume():
    s, _ = L.step(new(), {"e": "start"})
    s, a = L.step(s, {"e": "stop"})
    assert s["status"] == "stopped" and do(a, "stopped")
    s, _ = L.step(new(), {"e": "start"})
    s["status"] = "paused"
    s2, a = L.step(s, {"e": "resume"})
    assert s2["status"] == "live" and s2["phase"] == s["phase"] and s2["step"] == s["step"] and do(a, "ask")


def test_events_on_a_finished_or_stopped_lesson_do_nothing():
    s, _ = L.step(new(), {"e": "start"})
    s, _ = L.step(s, {"e": "stop"})
    s2, a = L.step(s, ans("right"))
    assert s2 == s and a == []


def test_unknown_event_is_harmless():
    s, _ = L.step(new(), {"e": "start"})
    s2, a = L.step(s, {"e": "dance"})
    assert s2 == s and a == []


# ---- the message classifier --------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,ev", [("stop", "stop"), ("let's stop here", "stop"), ("I'm stuck", "stuck"), ("give me a hint", "stuck"),
                                      ("show me the answer", "show_me"), ("say that again", "again"), ("explain it another way", "easier"),
                                      ("too easy, make it harder", "harder"), ("I don't know", "stuck"), ("what is the speed of light?", "other"),
                                      ("2.5 m/s^2", None), ("the second half", None), ("it halves", None)])
def test_classify(text, ev):
    assert L.classify(text) == ev
