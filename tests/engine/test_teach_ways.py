"""E16: teach it my way. A fake model only (ask.think is replaced), no paid call. A made-up stranger's subject."""
import asyncio
import json
import pathlib

import pytest

from misty_engine import chat, ways
from misty_engine.teach import ask, bank, live, plan
from misty_engine.teach import ways as tways
from test_teach_plan import cite, go, good_plan, Model, src, topic, run, QUOTES  # noqa: F401


def teach_way(ctx, key, text):
    return ways.set_way(ctx, key, text, "teach")


# ---- the kind --------------------------------------------------------------------------------------------------------
def test_teach_is_a_kind_in_both_lists():
    assert "teach" in ways.KINDS
    enum = next(t for t in chat.TOOLS if t["function"]["name"] == "way")["function"]["parameters"]["properties"]["kind"]["enum"]
    assert set(enum) == set(ways.KINDS)
    assert "teach" in chat.WAY_RULE and "cricket examples" not in chat.WAY_RULE.replace("Use cricket examples", "")


def test_a_teach_way_is_stored_and_read_back(fctx):
    assert tways.for_teaching(fctx) == []
    r = teach_way(fctx, "examples", "Use cricket examples when you teach him.")
    assert r["kind"] == "teach"
    assert tways.for_teaching(fctx) == ["Use cricket examples when you teach him."]


def test_only_teach_ways_are_read_and_at_most_six(fctx):
    ways.set_way(fctx, "tone", "Dry and understated.", "tone")
    for i in range(8):
        teach_way(fctx, f"way number {i} alpha{i}", f"Teaching way {i}.")
    got = tways.for_teaching(fctx)
    assert len(got) == 6 and "Dry and understated." not in got


def test_a_dropped_or_replaced_way_is_gone(fctx):
    teach_way(fctx, "examples", "Use cricket examples.")
    teach_way(fctx, "examples", "Use football examples.")
    assert tways.for_teaching(fctx) == ["Use football examples."]
    ways.drop(fctx, "examples")
    assert tways.for_teaching(fctx) == []


def test_teach_ways_are_not_style_for_the_other_prompts(fctx):
    teach_way(fctx, "examples", "Use cricket examples.")
    ways.set_way(fctx, "tone", "Dry and understated.", "tone")
    note = ways.style_note(fctx.con)
    assert "Dry" in note and "cricket" not in note
    assert "cricket" in ways.block(fctx.con)             # but her own list still shows it, so she can change or drop it


def test_hash(fctx):
    assert tways.hash_of([]) == "" and tways.hash_of(None) == ""
    a, b = tways.hash_of(["Use cricket examples.", "Slow on proofs."]), tways.hash_of(["slow on proofs", "use CRICKET examples"])
    assert a == b and a != "" and a != tways.hash_of(["Use cricket examples."])


# ---- plans are cached per topic: a way change leaves no stale plan -------------------------------------------------------
def test_the_table_has_the_hash_and_old_rows_are_empty(fctx):
    assert "ways_hash" in [r[1] for r in fctx.con.execute("pragma table_info(teach_plans)")]


def test_a_plan_made_without_ways_is_used_until_he_sets_one(fctx, src, topic, monkeypatch):
    go(fctx, topic, monkeypatch, good_plan(src))
    assert plan.get(fctx, topic) is not None
    teach_way(fctx, "examples", "Use cricket examples.")
    assert plan.get(fctx, topic) is None
    assert plan.queue_plan(fctx, topic)["action"] == "queued"      # remade in the background
    ways.drop(fctx, "examples")
    assert plan.get(fctx, topic) is not None                       # the old plan fits again: no ways


def test_a_plan_made_under_a_way_matches_that_way_only(fctx, src, topic, monkeypatch):
    teach_way(fctx, "examples", "Use cricket examples.")
    r, m = go(fctx, topic, monkeypatch, good_plan(src))
    assert r["status"] == "usable" and "cricket examples" in m.calls[0]["user"]       # make read the ways itself
    assert plan.get(fctx, topic)["id"] == r["id"]
    assert plan.queue_plan(fctx, topic)["action"] == "have"
    teach_way(fctx, "examples", "Use chess examples.")
    assert plan.get(fctx, topic) is None
    assert plan.queue_plan(fctx, topic)["action"] == "queued"


def test_the_newest_matching_plan_wins_not_the_newest_plan(fctx, src, topic, monkeypatch):
    r1, _ = go(fctx, topic, monkeypatch, good_plan(src))                 # no ways
    teach_way(fctx, "examples", "Use cricket examples.")
    r2, _ = go(fctx, topic, monkeypatch, good_plan(src))
    assert plan.get(fctx, topic)["id"] == r2["id"]
    ways.drop(fctx, "examples")
    assert plan.get(fctx, topic)["id"] == r1["id"]


def test_the_queued_job_reads_the_ways_when_it_runs(fctx, src, topic, monkeypatch):
    plan.queue_plan(fctx, topic)
    teach_way(fctx, "examples", "Use cricket examples.")                 # set after it was queued
    m = Model(good_plan(src))
    monkeypatch.setattr(ask, "think", m)
    job = fctx.con.execute("SELECT * FROM jobs WHERE kind='teach_plan'").fetchone()
    from types import SimpleNamespace
    run(plan.teach_plan_job(SimpleNamespace(id=job["id"], payload=json.loads(job["payload"])), fctx))
    assert "cricket examples" in m.calls[0]["user"] and plan.get(fctx, topic) is not None


def test_a_lesson_started_without_a_matching_plan_makes_one_now(fctx, src, topic, monkeypatch):
    go(fctx, topic, monkeypatch, good_plan(src))
    teach_way(fctx, "examples", "Use cricket examples.")
    monkeypatch.setattr(ask, "think", Model(good_plan(src)))
    r = run(live.start(fctx, topic))
    assert r["ok"] and r["plan_made_now"] and "your way" in r["plan_note"]
    assert live.current(fctx) is not None


# ---- the code check for "don't give me the answer first" -------------------------------------------------------------------
def leaky(src):
    p = good_plan(src)
    p["steps"][1]["explain"][0]["say"] = "In a negative split the second half is faster than the first."      # the check's answer is "faster"
    return p


def test_the_no_answer_way_is_found_in_plain_wordings():
    for w in ("Don't give me the answer, make me work for it.", "Never give the answer first.", "Make me work it out.",
              "Do not tell me the answer until I try."):
        assert tways.no_answer_first([w]), w
    for w in ("Use cricket examples.", "Quiz me in Spanish.", "Go slow on proofs.", "Give me the answer fast."):
        assert not tways.no_answer_first([w]), w


def test_a_plan_that_gives_the_answer_fails_when_he_asked_for_none(fctx, src, topic, monkeypatch):
    teach_way(fctx, "work", "Don't give me the answer first, make me work.")
    r, m = go(fctx, topic, monkeypatch, leaky(src))
    assert r["status"] == "rejected" and "answer" in r["why"] and len(m.calls) == 2           # one more go, told why
    assert "answer" in m.calls[1]["user"]
    assert plan.get(fctx, topic) is None


def test_the_same_plan_is_fine_without_that_way(fctx, src, topic, monkeypatch):
    r, _ = go(fctx, topic, monkeypatch, leaky(src))
    assert r["status"] == "usable"
    teach_way(fctx, "examples", "Use cricket examples.")                    # another way does not turn the check on
    r, _ = go(fctx, topic, monkeypatch, leaky(src))
    assert r["status"] == "usable"


def test_a_clean_plan_passes_the_check(fctx, src, topic, monkeypatch):
    teach_way(fctx, "work", "Don't give me the answer first, make me work.")
    r, _ = go(fctx, topic, monkeypatch, good_plan(src))
    assert r["status"] == "usable"


# ---- the live rule, the bank, the drill ----------------------------------------------------------------------------------
def test_the_live_rule_carries_his_ways_while_a_lesson_runs(fctx, src, topic, monkeypatch):
    go(fctx, topic, monkeypatch, good_plan(src))
    assert live.rule_block(fctx) == ""                                       # nothing live, nothing added
    run(live.start(fctx, topic))
    assert live.rule_block(fctx) == live.TEACH_RULE                         # live but no ways: unchanged
    live.stop(fctx)
    teach_way(fctx, "examples", "Use cricket examples.")
    monkeypatch.setattr(ask, "think", Model(good_plan(src)))
    run(live.start(fctx, topic))
    rb = live.rule_block(fctx)
    assert rb.startswith(live.TEACH_RULE) and "Use cricket examples." in rb and "never changes what is true" in rb


def test_the_bank_reads_his_ways(fctx, src, topic, monkeypatch):
    teach_way(fctx, "language", "Quiz me in Spanish.")
    m = Model("not json")
    monkeypatch.setattr(ask, "think", m)
    run(bank.make(fctx, topic))
    assert m.calls and "Quiz me in Spanish." in m.calls[0]["user"]


def test_the_drill_rule_carries_his_ways(fctx, monkeypatch):
    from misty_engine.teach import drill
    monkeypatch.setattr(drill, "current", lambda ctx: object())
    assert drill.rule_block(fctx) == drill.DRILL_RULE
    teach_way(fctx, "language", "Quiz me in Spanish.")
    assert "Quiz me in Spanish." in drill.rule_block(fctx)
    monkeypatch.setattr(drill, "current", lambda ctx: None)
    assert drill.rule_block(fctx) == ""


# ---- the sample rows --------------------------------------------------------------------------------------------------------
def test_sample_rows_for_the_tool_pick_check():
    import yaml
    rows = yaml.safe_load((pathlib.Path(__file__).parent / "fixtures" / "toolpick.sample.yaml").read_text())
    mine = [r for r in rows if r.get("tag") == "ways_teach"]
    says = [r for r in mine if r["expect_tool"] == "way"]
    assert len(says) >= 4 and all(r.get("expect_args_contains") == "teach" for r in says)
    assert {"remember", "care"} <= {r["expect_tool"] for r in mine}
