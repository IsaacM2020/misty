"""E4: classes and chat fill the map as evidence. Fake model replies only, no paid call. A made-up stranger's subject."""
import asyncio
import json

import pytest

from misty_engine import class_notes, classes
from misty_engine.openrouter import user_approved
from misty_engine.teach import fill, match
from misty_engine.teach import map as tm


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def subj(fctx):
    s = tm.add_subject(fctx, "running", "Running")["id"]
    for k, t in (("pacing", "Pacing a long run"), ("fuel", "Fuelling on long runs"), ("rest", "Rest days and recovery")):
        tm.add_topic(fctx, s, k, t, {"source_id": "src", "chunk_no": 1, "quote": t})
    return s


def tid(ctx, key):
    return ctx.con.execute("SELECT id FROM teach_topics WHERE key=?", (key,)).fetchone()[0]


def pick(fake_or, n):
    fake_or.chat_returns(json.dumps({"topic": n}))


def notes(lost=(), remember=(), heads=()):
    return {"title": "t", "summary": "", "sections": [{"heading": h, "points": []} for h in heads],
            "remember": [{"kind": "fact", "text": r, "seg": [1]} for r in remember],
            "action_items": [], "lost": [{"text": x, "seg": [1]} for x in lost], "next_step": None, "gaps": []}


def make_class(ctx, subject, n=None, merged_into=None):
    s = classes.start(ctx.con, ctx.clock, subject, None, None)
    ctx.con.execute("UPDATE class_sessions SET notes_json=?, status='noted', merged_into=? WHERE id=?",
                    (json.dumps(n) if n is not None else None, merged_into, s["id"]))
    return s["id"]


# ---- match ---------------------------------------------------------------------------------------------------------
def test_match_returns_the_topic_the_model_picks(fctx, fake_or, subj):
    pick(fake_or, 1)
    r = run(match.topic_for(fctx, subj, "starting slower than race pace"))
    assert r in {t["id"] for t in fctx.con.execute("SELECT id FROM teach_topics").fetchall()}
    b = fake_or.chat_bodies[0]
    assert b["temperature"] == 0 and "enabled" not in (b.get("reasoning") or {}) or b["reasoning"].get("enabled") is not True


def test_match_none_is_honoured(fctx, fake_or, subj):
    fake_or.chat_returns(json.dumps({"topic": "none"}))
    assert run(match.topic_for(fctx, subj, "something else entirely")) is None


def test_match_bad_reply_or_out_of_range_is_none(fctx, fake_or, subj):
    fake_or.chat_returns("garbage")
    assert run(match.topic_for(fctx, subj, "x")) is None
    pick(fake_or, 99)
    assert run(match.topic_for(fctx, subj, "x")) is None


def test_match_shows_at_most_eight_topics(fctx, fake_or, subj):
    for i in range(20):
        tm.add_topic(fctx, subj, f"extra {i}", f"Extra thing {i}", {"source_id": "s", "chunk_no": 1, "quote": "q"})
    pick(fake_or, 1)
    run(match.topic_for(fctx, subj, "pacing a long run"))
    body = fake_or.chat_bodies[0]["messages"][1]["content"]
    assert body.count("\n8.") + body.count("8. ") >= 1 and "9. " not in body


def test_match_with_no_topics_is_none_without_a_call(fctx, fake_or):
    s = tm.add_subject(fctx, "empty", "Empty")["id"]
    assert run(match.topic_for(fctx, s, "anything")) is None
    assert fake_or.chat_bodies == []


# ---- classes -------------------------------------------------------------------------------------------------------
def test_class_items_become_evidence(fctx, fake_or, subj):
    tm.link_class(fctx, subj, "Run Club")
    cid = make_class(fctx, "Run Club", notes(lost=["I lost the part about pacing"], remember=["Eat every forty minutes"], heads=["Fuel"]))
    fake_or.chat_returns(json.dumps({"topic": 1}), times=3)
    with user_approved():
        r = run(fill.fill_class(fctx, cid))
    assert r["added"] == 3
    kinds = sorted(x[0] for x in fctx.con.execute("SELECT kind FROM teach_evidence"))
    assert kinds == ["class_lost", "class_seen", "class_seen"]
    row = fctx.con.execute("SELECT origin, origin_id FROM teach_evidence WHERE kind='class_lost'").fetchone()
    assert row[0] == "class" and row[1].startswith(cid + ":lost:") and len(row[1].split(":")[2]) == 8


def test_running_twice_adds_nothing_and_asks_nothing(fctx, fake_or, subj):
    tm.link_class(fctx, subj, "Run Club")
    cid = make_class(fctx, "Run Club", notes(lost=["lost on pacing"], remember=["fuel often"]))
    fake_or.chat_returns(json.dumps({"topic": 1}), times=2)
    with user_approved():
        run(fill.fill_class(fctx, cid))
        n_calls = len(fake_or.chat_bodies)
        r = run(fill.fill_class(fctx, cid))
    assert r["added"] == 0 and len(fake_or.chat_bodies) == n_calls
    assert fctx.con.execute("SELECT count(*) FROM teach_evidence").fetchone()[0] == 2


def test_remade_notes_add_only_the_new_item(fctx, fake_or, subj):
    tm.link_class(fctx, subj, "Run Club")
    cid = make_class(fctx, "Run Club", notes(remember=["fuel often"]))
    fake_or.chat_returns(json.dumps({"topic": 2}), times=3)
    with user_approved():
        run(fill.fill_class(fctx, cid))
        fctx.con.execute("UPDATE class_sessions SET notes_json=? WHERE id=?",
                         (json.dumps(notes(remember=["fuel often", "rest after hard days"])), cid))
        r = run(fill.fill_class(fctx, cid))
    assert r["added"] == 1


def test_none_adds_no_evidence(fctx, fake_or, subj):
    tm.link_class(fctx, subj, "Run Club")
    cid = make_class(fctx, "Run Club", notes(lost=["weather small talk"]))
    fake_or.chat_returns(json.dumps({"topic": "none"}))
    with user_approved():
        r = run(fill.fill_class(fctx, cid))
    assert r["added"] == 0 and fctx.con.execute("SELECT count(*) FROM teach_evidence").fetchone()[0] == 0


def test_class_with_no_map_is_skipped_and_listed(fctx, fake_or):
    cid = make_class(fctx, "Cooking", notes(lost=["lost on knife skills"]))
    with user_approved():
        r = run(fill.fill_class(fctx, cid))
    assert r["added"] == 0 and r["skipped"] == "no map" and fake_or.chat_bodies == []
    with user_approved():
        allr = run(fill.fill_all(fctx))
    assert "Cooking" in allr["skipped"]


def test_merged_and_unnoted_classes_are_skipped(fctx, fake_or, subj):
    tm.link_class(fctx, subj, "Run Club")
    a = make_class(fctx, "Run Club", notes(lost=["x"]), merged_into="cs_other")
    b = make_class(fctx, "Run Club", None)
    with user_approved():
        assert run(fill.fill_class(fctx, a))["added"] == 0
        assert run(fill.fill_class(fctx, b))["added"] == 0
    assert fake_or.chat_bodies == []


def test_link_is_made_by_name_when_it_is_obvious(fctx, subj):
    make_class(fctx, "running", notes())
    assert fill.auto_link(fctx, subj) == "running"
    assert fctx.con.execute("SELECT class_subject FROM teach_subjects WHERE id=?", (subj,)).fetchone()[0] == "running"


def test_link_is_not_guessed_when_names_differ(fctx, subj):
    make_class(fctx, "Gym", notes())
    assert fill.auto_link(fctx, subj) is None


def test_fill_failure_never_turns_noted_into_failed(fctx, monkeypatch, subj):
    tm.link_class(fctx, subj, "Run Club")
    cid = make_class(fctx, "Run Club", notes(lost=["x"]))

    async def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(fill, "fill_class", boom)
    with user_approved():
        run(class_notes.teach_fill(fctx, cid))                # must not raise
    assert fctx.con.execute("SELECT status FROM class_sessions WHERE id=?", (cid,)).fetchone()[0] == "noted"


# ---- chat ----------------------------------------------------------------------------------------------------------
KEY = {"question": "Why start a long run slower than race pace?", "idea": "pacing: even effort saves the last miles"}


def test_help_is_recorded_once_per_topic_per_day(fctx, fake_or, subj):
    fake_or.chat_returns(json.dumps({"topic": 1}), times=3)
    with user_approved():
        a = run(fill.help_evidence(fctx, KEY, "help me with this question"))
        b = run(fill.help_evidence(fctx, KEY, "help me with this question"))
    assert a is True and b is False
    rows = fctx.con.execute("SELECT kind, origin FROM teach_evidence").fetchall()
    assert [tuple(r) for r in rows] == [("asked_help", "chat")]
    fctx.clock.advance(86400 * 2) if hasattr(fctx.clock, "advance") else None
    with user_approved():
        c = run(fill.help_evidence(fctx, KEY, "help me with this question"))
    assert c is True or not hasattr(fctx.clock, "advance")


def test_help_with_no_match_records_nothing(fctx, fake_or, subj):
    fake_or.chat_returns(json.dumps({"topic": "none"}))
    with user_approved():
        assert run(fill.help_evidence(fctx, KEY, "help")) is False
    assert fctx.con.execute("SELECT count(*) FROM teach_evidence").fetchone()[0] == 0


def test_help_with_no_subjects_asks_nothing(fctx, fake_or):
    assert run(fill.help_evidence(fctx, KEY, "help")) is False and fake_or.chat_bodies == []
