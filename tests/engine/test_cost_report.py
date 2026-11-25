"""The cost report: totals by job and day, and each reply broken into its model calls."""
import json

import pytest

from misty_engine import cost_report


def add_spend(ctx, purpose, usd, ts=None, model="anthropic/claude-haiku-5.5"):
    ctx.spend.con.execute("INSERT INTO spend(ts, provider, model, job_id, build_id, usd, purpose) VALUES (?,?,?,?,?,?,?)",
                          (ts or ctx.clock.now(), "openrouter", model, None, None, usd, purpose))


def add_turn(ctx, q, a, meta):
    with ctx.con:
        ctx.con.execute("INSERT INTO chat_messages(role,text,meta,created_at) VALUES ('user',?,NULL,?)", (q, ctx.clock.now()))
        ctx.con.execute("INSERT INTO chat_messages(role,text,meta,created_at) VALUES ('assistant',?,?,?)",
                        (a, json.dumps(meta), ctx.clock.now()))


def test_totals_group_jobs_under_plain_names_and_split_days(ctx):
    now = ctx.clock.now()
    add_spend(ctx, "chat", 0.001)
    add_spend(ctx, "chat", 0.002)
    add_spend(ctx, "act", 0.01)
    add_spend(ctx, "stream", 0.02)
    add_spend(ctx, None, 0.5, ts=now - 3 * 86400)
    add_spend(ctx, "chat", 9.0, ts=now - 30 * 86400)                    # outside the 7 day window
    r = cost_report.breakdown(ctx.con, now, days=7)
    names = {g["label"]: g for g in r["by_purpose"]}
    assert names["Chat replies"]["calls"] == 2 and names["Chat replies"]["usd"] == 0.003
    assert names["Doing things on your Mac"]["usd"] == 0.03 and names["Doing things on your Mac"]["calls"] == 2
    assert "Early build (before jobs were labelled)" in names
    assert r["today"] == pytest.approx(0.033) and r["window"] == pytest.approx(0.533)
    assert len(r["by_day"]) == 2 and r["by_day"][0]["usd"] == pytest.approx(0.033)


def test_each_reply_lists_its_model_calls_and_tools(ctx):
    meta = {"usd": 0.0031, "calls": [{"name": "calendar", "args": {}, "result": "{}"}],
            "rounds": [{"in": 5000, "cached": 4000, "out": 40, "usd": 0.0015, "exact": True, "tools": ["calendar"]},
                       {"in": 5400, "cached": 4000, "out": 90, "usd": 0.0016, "exact": True, "tools": []}],
            "tool_usd": []}
    add_turn(ctx, "what's on today", "Physics at 2.", meta)
    add_turn(ctx, "old question", "old answer", {"usd": 0.0005, "calls": []})        # from before rounds were recorded
    msgs = cost_report.breakdown(ctx.con, ctx.clock.now(), days=1, messages=5)["messages"]
    assert [m["question"] for m in msgs] == ["old question", "what's on today"]
    new = msgs[1]
    assert new["tools"] == ["calendar"] and len(new["rounds"]) == 2 and new["rounds"][0]["cached"] == 4000
    assert msgs[0]["rounds"] == [] and msgs[0]["usd"] == 0.0005


def test_window_is_clamped(ctx):
    assert cost_report.breakdown(ctx.con, ctx.clock.now(), days=0)["days"] == 1
    assert cost_report.breakdown(ctx.con, ctx.clock.now(), days=9999)["days"] == 90
