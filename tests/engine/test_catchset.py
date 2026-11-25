"""G1: the offline catch test set. Counts, labels and a free arithmetic check of the files that must be right."""
import re
from collections import Counter

from catchset import FURNITURE, KINDS, load, split


def test_fifty_files_each_with_one_label_and_the_fixed_mix():
    cases = load()
    assert len(cases) == 50 and len({c.id for c in cases}) == 50
    s = split(cases)
    assert (len(s["error"]), len(s["correct"]), len(s["rough"])) == (20, 20, 10)
    assert sum(c.provable for c in cases) == 15
    assert Counter(c.expect["kind"] for c in s["error"]) == {"sign": 6, "arithmetic": 5, "copied": 4, "unit": 3, "formula": 2}
    assert Counter(c.subject for c in cases) == {"maths": 20, "physics": 15, "chemistry": 15}
    assert sum(c.data for c in cases) >= 6


def test_provable_means_sign_arithmetic_or_copied_and_only_errors_can_be_provable():
    for c in load():
        assert c.provable == bool(c.expect and c.expect["kind"] in ("sign", "arithmetic", "copied")), c.id
        assert not c.expect or c.expect["kind"] in KINDS


def test_each_error_line_points_at_a_real_working_line():
    for c in load():
        if c.expect:
            assert 1 <= c.expect["line"] <= len(c.working), c.id
            assert c.error_text and c.error_text not in FURNITURE


def test_working_never_has_furniture_between_lines_and_never_starts_on_furniture():
    for c in load():
        w = c.lines[c.working_from - 1:]
        assert w and w[0] not in FURNITURE, c.id
        idx = [i for i, ln in enumerate(w) if ln in FURNITURE]
        assert all(i >= len(c.working) for i in idx), c.id            # furniture only after the working
        assert len(c.working) >= 1 and c.question, c.id


def test_every_file_has_some_page_furniture_to_ignore():
    assert all(any(ln in FURNITURE for ln in c.lines) for c in load())


def test_rough_files_look_rough_and_correct_ones_do_not():
    rough = re.compile(r"~~|\?\?|→|\(.*\)$|oops|so x =|check")
    for c in split()["rough"]:
        assert any(rough.search(ln) for ln in c.working), c.id
    assert any("~~" in ln for c in split()["rough"] for ln in c.working)
    assert any("??" in ln for c in split()["rough"] for ln in c.working)
    for c in split()["correct"]:
        assert not any("~~" in ln or "??" in ln for ln in c.working), c.id


# ---- the files that must be right really are right (my own sums, a tiny evaluator, nothing from the engine)
def _num(s: str):
    s = (s.replace("×", "*").replace("−", "-").replace("÷", "/").replace("²", "**2").replace("^", "**").strip())
    s = re.sub(r"\b(?:J|N|W|m|s|g|mol|kJ|dm³|cm³|kg|Ω|%)\b.*$", "", s).strip()
    if not s or re.search(r"[A-Za-zΔ\[\]]", s):
        return None
    try:
        return eval(s, {"__builtins__": {}})                              # noqa: S307  only digits and operators got here
    except Exception:
        return None


def _checks(line: str):
    """For a line like '0.5 × 4 × 9 = 18', yield (left value, right value) for every pair of neighbouring evaluable parts."""
    parts = [p for p in re.split(r"=|→", line)]
    vals = [_num(p) for p in parts]
    for a, b in zip(vals, vals[1:]):
        if a is not None and b is not None:
            yield a, b


def test_all_arithmetic_in_the_correct_and_rough_files_holds():
    bad = []
    for c in split()["correct"] + split()["rough"]:
        for ln in c.working:
            if "~~" in ln:
                continue
            for a, b in _checks(ln):
                if abs(a - b) > max(0.02 * abs(b), 0.011):
                    bad.append((c.id, ln, a, b))
    assert not bad, bad


def test_each_arithmetic_error_line_really_has_a_wrong_sum():
    for c in split()["error"]:
        if c.expect["kind"] == "arithmetic":
            assert any(abs(a - b) > max(0.02 * abs(b), 0.011) for a, b in _checks(c.error_text)), c.id


def test_each_copied_number_is_not_in_the_question():
    nums = lambda t: set(re.findall(r"\d+(?:\.\d+)?", t))                 # noqa: E731
    for c in split()["error"]:
        if c.expect["kind"] == "copied":
            q = nums(" ".join(c.question))
            assert nums(c.error_text) - q, c.id
