import pytest

from misty_engine.registry import names as N


@pytest.mark.parametrize("raw,kind,kw,expect", [
    ("", "person", {}, "EMPTY"),
    ("+65 9123 4567", "person", {}, "PHONE"),
    ("aarav@school.edu", "person", {}, "EMAIL"),
    ("MC SA2 OVER chat member", "person", {}, "CHAT_WORD"),
    ("MC SA2 OVER", "person", {"chat_title": "MC SA2 OVER", "chat_kind": "group"}, "CHAT_TITLE"),
    ("Isaac Miranda", "person", {"user_names": frozenset({"isaac", "isaac miranda"})}, "USER"),
    ("A", "person", {}, "SINGLE_CHAR"),
    ("2026", "topic", {}, "NUMERIC"),
    ("Aarav Shah", "person", {}, None),
    ("Python", "topic", {}, None),
    ("\U0001F3CF", "person", {}, "EMPTY"),                  # emoji only normalises to nothing
    ("Mr.", "person", {}, "EMPTY"),                          # an honorific alone is not a name
    ("Ms", "topic", {}, None),
])
def test_names_table(raw, kind, kw, expect):
    assert N.reject_reason(raw, kind, **kw) == expect


def test_core_tokens_strip_chat_artifacts_for_people():
    assert N.core_tokens("Pranav Asrani Real") == ("pranav", "asrani")
    assert N.core_tokens("Aarav (chat member)") == ("aarav",)
    assert N.core_tokens("Study Group", kind="topic") == ("study", "group")


def test_core_tokens_strip_honorifics_only_for_people():
    assert N.core_tokens("Ms. Janaki") == ("janaki",)
    assert N.core_tokens("Dr Strange") == ("strange",)
    assert N.core_tokens("Ms Excel", kind="topic") == ("ms", "excel")


@pytest.mark.parametrize("a,b,ok", [
    (("pranav",), ("pranav", "asrani"), True),
    (("asrani",), ("pranav", "asrani"), True),
    (("pranav", "asrani"), ("pranav", "naveen", "asrani"), True),
    (("pranav", "asrani"), ("pranav", "kumar"), False),
    (("naveen",), ("pranav", "naveen", "asrani"), False),   # a middle name alone is not an identity
    (("janaki",), ("janaki", "kota"), True),
    (("rahul",), ("priya", "rahul", "shah"), False),
])
def test_compatible(a, b, ok):
    assert N.compatible(a, b) is ok


def test_canonical_prefers_clean_longest_then_most_mentioned():
    s = N.canonical_score
    assert s("Pranav Naveen Asrani", 2) > s("Pranav Asrani Real", 9)      # 'real' is an artifact token
    assert s("Pranav Asrani", 5) > s("Pranav", 50)
    assert s("MC SA2 Chat", 3, frozenset({"mc sa2 chat"})) < s("Aarav", 1)
