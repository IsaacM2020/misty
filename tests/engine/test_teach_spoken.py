"""E8: his voice. Spoken numbers read right, long questions are cut for the ear, and a number said in words is graded."""
import pytest

from misty_engine.teach import keys, spoken

CASES = [
    ("nine point eight", 9.8, ""),
    ("nine point eight metres per second squared", 9.8, "metres per second squared"),
    ("twelve metres per second", 12, "metres per second"),
    ("twenty five", 25, ""),
    ("twenty-five newtons", 25, "newtons"),
    ("a hundred", 100, ""),
    ("one hundred and five", 105, ""),
    ("three hundred forty two", 342, ""),
    ("two thousand", 2000, ""),
    ("two thousand and twenty", 2020, ""),
    ("one million", 1_000_000, ""),
    ("fifteen hundred", 1500, ""),
    ("minus five", -5, ""),
    ("negative three point five", -3.5, ""),
    ("zero", 0, ""),
    ("nought point five", 0.5, ""),
    ("point five", 0.5, ""),
    ("one over four", 0.25, ""),
    ("three over eight", 0.375, ""),
    ("two point five zero", 2.5, ""),
    ("it is nine point eight", 9.8, ""),
    ("five squared", 25, ""),
    ("the answer is forty", 40, ""),
    ("seventy two kilometres per hour", 72, "kilometres per hour"),
    ("ninety nine point nine percent", 99.9, "percent"),
    ("three times ten to the minus four", 3e-4, ""),
    ("six point zero two times ten to the twenty three", 6.02e23, ""),
    ("eleven", 11, ""),
    ("thirteen", 13, ""),
    ("nineteen", 19, ""),
    ("eighty", 80, ""),
    ("ninety one", 91, ""),
    ("one hundred twenty", 120, ""),
    ("a thousand", 1000, ""),
    ("four point nine joules", 4.9, "joules"),
    ("I think it's eight", 8, ""),
    ("minus zero point five", -0.5, ""),
    ("Twelve Metres per Second.", 12, "metres per second"),
    ("one hundred and fifty grams", 150, "grams"),
    ("six point two eight", 6.28, ""),
]


@pytest.mark.parametrize("text,val,tail", CASES)
def test_spoken_number(text, val, tail):
    got = spoken.words_to_number(text)
    assert got is not None, text
    assert got[0] == pytest.approx(val, rel=1e-9) and got[1].lower().strip(" .") == tail


def test_there_are_at_least_forty_forms():
    assert len(CASES) >= 40 - 0


@pytest.mark.parametrize("text", ["I have no idea", "show me", "banana", "", "why does that work", "x" * 300])
def test_no_number_in_it(text):
    assert spoken.words_to_number(text) is None


def test_read_number_uses_words_only_when_there_is_no_digit():
    assert keys.read_number("nine point eight metres per second squared") == (pytest.approx(9.8), "metres per second squared")
    assert keys.read_number("12 metres") == (12.0, "metres")           # a digit wins, words are not tried
    assert keys.read_number("I have no idea") is None


def test_a_spoken_number_is_graded_by_the_key():
    key = {"expr": "49/5", "unit": "m/s^2", "sig_figs": 2}
    assert keys._grade_number(key, "nine point eight metres per second squared")["verdict"] == "right"
    assert keys._grade_number(key, "seven point eight metres per second squared")["verdict"] == "wrong"
    assert keys._grade_number(key, "nine point eight metres per second")["verdict"] != "right"       # the unit is wrong
    assert keys._grade_number({"expr": "180/2.5", "unit": "km/h"}, "seventy two kilometres per hour")["verdict"] == "right"


# ---- voice-sized text --------------------------------------------------------------------------------------------
def test_a_short_question_is_untouched():
    q = "What is a negative split in a run?"
    assert spoken.for_ear(q) == q


def test_a_long_question_is_two_parts_with_a_pause_and_no_words_lost():
    q = ("A runner covers the first half of a ten kilometre race in twenty six minutes and the second half in twenty four minutes "
         "after settling into a steady rhythm. What was the pace pattern called, and was it a good plan?")
    assert len(q.split()) > 30
    out = spoken.for_ear(q)
    assert "\n\n" in out
    assert out.replace("\n\n", " ").split() == q.split()
    first = out.split("\n\n")[0]
    assert first.endswith(".") and len(first.split()) <= 30


def test_one_long_sentence_is_cut_at_a_comma_when_there_is_one():
    q = ("Think about a runner who starts slowly and then speeds up steadily through the second half of a long race, "
         "and tell me what we call that kind of pacing plan in running.")
    out = spoken.for_ear(q)
    assert out.replace("\n\n", " ").split() == q.split()
    assert "\n\n" in out
