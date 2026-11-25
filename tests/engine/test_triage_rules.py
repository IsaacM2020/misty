from misty_engine.config import default_pinned_path
from misty_engine.jev_wire import Answer
from misty_engine.scrub import find_secrets
from misty_engine.triage import decide_triage, decide_worth
from misty_engine.types import approx_tokens


def A(c, p=0.9):
    return Answer((c,), p)


REST = {"action": A("none"), "area": A("school"), "urgency": A("none")}


def test_matters_no_means_index_only():
    d = decide_triage({"matters": A("no"), **REST})
    assert not d.note and d.suggestion_kind is None


def test_low_confidence_matters_keeps_item():
    assert decide_triage({"matters": A("no", 0.55), **REST}).note


def test_action_makes_suggestion():
    d = decide_triage({"matters": A("yes"), "action": A("reply"), "area": A("school"), "urgency": A("today")})
    assert d.suggestion_kind == "reply" and not d.badge


def test_urgency_now_sets_badge():
    d = decide_triage({"matters": A("yes"), "action": A("task_or_deadline"), "area": A("school"), "urgency": A("now")})
    assert d.badge and d.suggestion_kind == "task"


def test_action_none_no_suggestion_even_if_urgent():
    d = decide_triage({"matters": A("yes"), "action": A("none"), "area": A("school"), "urgency": A("now")})
    assert d.suggestion_kind is None and not d.badge


def test_unsure_action_makes_no_card():
    d = decide_triage({"matters": A("yes"), "action": A("reply", 0.4), "area": A("school"), "urgency": A("now")})
    assert d.suggestion_kind is None


def test_area_other_is_none():
    assert decide_triage({"matters": A("yes"), "action": A("none"), "area": A("other"), "urgency": A("none")}).area is None


def test_worth_keeps_when_unsure():
    assert decide_worth(A("no", 0.5)) and not decide_worth(A("no", 0.8)) and decide_worth(A("yes", 0.9))


def test_bootstrap_card_is_small_and_clean():
    card = (default_pinned_path().parent / "bootstrap-about-isaac.md").read_text()
    assert approx_tokens(card) <= 500 and not find_secrets(card, "file")
