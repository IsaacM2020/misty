import json
import itertools

import pytest

from misty_engine.registry.resolve import AliasIndex, Mention, MentionCtx, evidence, resolve_mention
from misty_engine.registry.store import Registry

_seq = itertools.count(1)


@pytest.fixture
def reg(con, clock):
    return Registry(con, clock)


@pytest.fixture
def mk(con, reg, add_item):
    def make(kind, name, n=1, chat_id=None, sender=None, note_with=()):
        eid = reg.new_entity(kind, name, status="active", path=f"{kind}s/{next(_seq)}.md")
        for i in range(n):
            iid = f"it_{next(_seq)}"
            add_item(iid, source="whatsapp", kind="chat_week")
            meta = {}
            if chat_id:
                meta["chat_id"] = chat_id
            if sender:
                meta["sender"] = sender
            con.execute("UPDATE items SET meta=? WHERE id=?", (json.dumps(meta), iid))
            reg.add_mention(iid, eid, name)
            for other in note_with:
                reg.add_mention(iid, other, name)
        reg.add_alias(eid, name, n=n)
        return eid
    return make


def test_exact_multi_token_resolves_r1(reg, mk):
    a = mk("person", "Aarav Shah", n=3)
    d = resolve_mention(reg, Mention("it_x", "person", "Aarav  SHAH"))
    assert (d.action, d.entity_id, d.reason) == ("resolved", a, "R1")


def test_ms_janaki_single_candidate_with_evidence(reg, mk):
    j = mk("person", "Janaki Kota", n=3, chat_id="c_school")
    d = resolve_mention(reg, Mention("it_x", "person", "Ms Janaki", ctx=MentionCtx(chat_id="c_school")))
    assert (d.action, d.entity_id) == ("resolved", j)


def test_one_token_without_evidence_is_plain(reg, mk):
    mk("person", "Pranav Asrani", n=1)
    assert resolve_mention(reg, Mention("it_x", "person", "Pranav")).action == "plain"


def test_one_token_no_candidate_is_plain(reg):
    d = resolve_mention(reg, Mention("it_x", "person", "Rahul"))
    assert (d.action, d.reason) == ("plain", "ONE_TOKEN_NO_CANDIDATE")


def test_two_tokens_no_candidate_is_new(reg):
    assert resolve_mention(reg, Mention("it_x", "person", "Aarav Shah")).action == "new"


def test_known_one_word_name_resolves_without_evidence(reg):
    mom = reg.new_entity("person", "Mom", status="candidate", origin="contact")
    d = resolve_mention(reg, Mention("it_x", "person", "Mom"))
    assert (d.action, d.entity_id, d.reason) == ("resolved", mom, "R4_KNOWN")


def test_one_word_name_that_already_had_a_page_resolves_without_evidence(reg):
    """Migration: a one-word name whose page already existed in the user's wiki is accepted, as a contact is."""
    kunal = reg.new_entity("person", "Kunal", status="active", origin="migration")
    reg.add_alias(kunal, "Kunal", source="migration", n=0)
    d = resolve_mention(reg, Mention("it_x", "person", "Kunal"))
    assert (d.action, d.entity_id, d.reason) == ("resolved", kunal, "R4_KNOWN")


def test_one_word_name_from_a_stub_page_still_needs_evidence(reg):
    """A migrated stub page (no summary, so no real page) was never accepted: it stays a candidate and plain text."""
    adi = reg.new_entity("person", "Adi", status="candidate", origin="migration")
    reg.add_alias(adi, "Adi", source="migration", n=0)
    d = resolve_mention(reg, Mention("it_x", "person", "Adi"))
    assert (d.action, d.reason) == ("plain", "ONE_TOKEN_NO_EVIDENCE")


def test_class_goes_to_judge(reg, mk):
    ids = [mk("person", "Pranav Asrani", n=5), mk("person", "Pranav Naveen Asrani", n=2)]
    d = resolve_mention(reg, Mention("it_x", "person", "Pranav"))
    assert d.action == "judge" and set(d.candidates) == set(ids) and d.reason == "CLASS"


def test_conflicting_surnames_use_context(reg, mk):
    a = mk("person", "Pranav Asrani", n=4, chat_id="c1")
    mk("person", "Pranav Kumar", n=4, chat_id="c2")
    d = resolve_mention(reg, Mention("it_x", "person", "Pranav", ctx=MentionCtx(chat_id="c1")))
    assert (d.action, d.entity_id) == ("resolved", a)


def test_conflicting_surnames_without_context_stay_plain(reg, mk):
    mk("person", "Pranav Asrani", n=4)
    mk("person", "Pranav Kumar", n=4)
    assert resolve_mention(reg, Mention("it_x", "person", "Pranav")).action == "plain"


def test_pranav_asrani_and_kumar_are_never_a_class(reg, mk):
    a = mk("person", "Pranav Asrani", n=2)
    k = mk("person", "Pranav Kumar", n=2)
    reg.add_alias(a, "Pranav")           # both carry the shared one-token alias
    reg.add_alias(k, "Pranav")
    d = resolve_mention(reg, Mention("it_x", "person", "Pranav"))
    assert d.action != "judge"


def test_counterpart_rule(reg, mk):
    p = mk("person", "Pranav Asrani Real", n=2)
    d = resolve_mention(reg, Mention("it_x", "person", "Pranav", ctx=MentionCtx(chat_kind="1to1", counterpart=p)))
    assert d.reason == "R3" and d.entity_id == p


def test_counterpart_with_other_surname_is_not_r3(reg, mk):
    p = mk("person", "Pranav Asrani", n=2)
    d = resolve_mention(reg, Mention("it_x", "person", "Pranav Kumar", ctx=MentionCtx(chat_kind="1to1", counterpart=p)))
    assert d.reason != "R3" and d.action == "new"


def test_handle_resolves_when_text_agrees(reg, mk):
    p = mk("person", "Aarav Shah", n=1)
    reg.add_handle(p, "email", "aarav@x.com")
    ctx = MentionCtx(sender_handle=("email", "aarav@x.com"))
    assert resolve_mention(reg, Mention("it_x", "person", "Aarav", ctx=ctx)).reason == "R2"
    assert resolve_mention(reg, Mention("it_x", "person", "Zed Roe", ctx=ctx)).action == "new"


def test_kind_isolation(reg, mk):
    t = mk("topic", "Python", n=3)
    d = resolve_mention(reg, Mention("it_x", "person", "Python"))
    assert d.action in ("plain", "new") and d.entity_id != t
    d2 = resolve_mention(reg, Mention("it_x", "topic", "python"))
    assert (d2.action, d2.entity_id) == ("resolved", t)


def test_topic_unknown_needs_similarity_and_dup_alias_goes_to_judge(reg, mk):
    assert resolve_mention(reg, Mention("i", "topic", "Quantum Biology")).reason == "NEEDS_SIMILARITY"
    a, b = mk("topic", "SAT Prep", n=2), mk("topic", "SAT Prep", n=2)
    d = resolve_mention(reg, Mention("i", "topic", "sat prep"))
    assert d.action == "judge" and d.reason == "DUP_ALIAS" and set(d.candidates) == {a, b}


def test_rejects_chat_title_and_members(reg):
    ctx = MentionCtx(chat_title="MC SA2 OVER", chat_kind="group")
    assert resolve_mention(reg, Mention("i", "person", "MC SA2 OVER", ctx=ctx)).reason == "CHAT_TITLE"
    assert resolve_mention(reg, Mention("i", "person", "Dev (chat member)")).action == "rejected"


@pytest.mark.parametrize("raw", ["+65 9123 4567", "\U0001F3CF", "Ms.", "A", "x@y.io", "  "])
def test_hostile_person_names_are_rejected_never_crash(reg, raw):
    d = resolve_mention(reg, Mention("i", "person", raw))
    assert d.action == "rejected" and d.reason


def test_user_is_rejected(reg):
    d = resolve_mention(reg, Mention("i", "person", "Isaac"), user_names=frozenset({"isaac", "isaac miranda"}))
    assert (d.action, d.reason) == ("rejected", "USER")


def test_tied_compatible_leaders_become_a_class(reg, mk):
    a = mk("person", "Pranav Asrani", n=3, chat_id="c1")
    b = mk("person", "Pranav Naveen Asrani", n=3, chat_id="c1")
    mk("person", "Pranav Kumar", n=3, chat_id="c2")
    d = resolve_mention(reg, Mention("i", "person", "Pranav", ctx=MentionCtx(chat_id="c1")))
    assert d.action == "judge" and set(d.candidates) == {a, b} and d.reason == "CLASS"


def test_evidence_pieces(reg, mk):
    p = mk("person", "Aarav Shah", n=2, chat_id="c1", sender="aarav@x.com")
    q = mk("person", "Priya Rao", n=1)
    ctx = MentionCtx(chat_id="c1", sender_handle=("email", "aarav@x.com"), note_entities=(q,))
    m = Mention("i", "person", "Aarav", ctx=ctx)
    assert evidence(reg, p, m) == 2 + 2 + 0
    assert evidence(reg, q, Mention("i", "person", "Priya", ctx=MentionCtx())) == 0


def test_index_gives_same_answer_as_scan(reg, mk):
    mk("person", "Pranav Asrani", n=4, chat_id="c1")
    mk("person", "Pranav Kumar", n=4, chat_id="c2")
    mk("person", "Aarav Shah", n=2)
    idx = AliasIndex.build(reg)
    for raw in ("Pranav", "Aarav", "Aarav Shah", "Kumar", "Zed Roe"):
        m = Mention("i", "person", raw, ctx=MentionCtx(chat_id="c1"))
        assert resolve_mention(reg, m, index=idx) == resolve_mention(reg, m)
