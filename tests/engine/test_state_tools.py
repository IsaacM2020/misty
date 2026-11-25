"""R1 (tool routing): judge, answer_ask and lets_go are sent only in their state, and the old open_url tool is a move."""
import json

import pytest

from misty_engine import brief, chat, gate

from test_chat import ScriptBrain, call, nothing, no, say

STATE_ONLY = {"judge", "answer_ask", "lets_go"}


def names(ctx, exam_set=None):
    return {t["function"]["name"] for t in chat.tools_for(ctx, exam_set)}


def test_outside_their_state_the_always_on_list_is_28(ctx):
    n = names(ctx)
    assert not (n & STATE_ONLY) and "open_url" not in n and len(n) == 29


def test_judge_is_sent_only_while_tutoring(ctx):
    assert "judge" not in names(ctx)
    chat._save(ctx, "assistant", "Ball falls 5 m.", {"calls": [{"name": "solve", "args": {}, "result": "{}"}]})
    assert chat._tutoring(ctx) and "judge" in names(ctx)
    ctx.clock.t += chat.TUTORING_SECONDS + 60                      # half an hour later the lesson is over
    assert "judge" not in names(ctx)


def test_answer_ask_is_sent_only_while_an_ask_waits(ctx):
    assert "answer_ask" not in names(ctx)
    ctx.con.execute("INSERT INTO told(id, at, how, said, text, topic, topic_text, source_name, key, urgency) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("t1", ctx.clock.now(), "ask", "Want to hear it?", "the news", "f1", "India vs NZ", "Cricinfo", "k1", "soon"))
    ctx.con.commit()
    assert gate.pending_ask(ctx.con, ctx.clock.now()) is not None and "answer_ask" in names(ctx)
    ctx.con.execute("UPDATE told SET feedback='declined'")
    assert "answer_ask" not in names(ctx)


def test_lets_go_is_sent_only_when_something_was_offered(ctx):
    assert "lets_go" not in names(ctx)
    ctx.extras = {"openable": {"at": ctx.clock.now(), "items": [{"id": "u1", "kind": "link", "target": "https://a.test/x", "label": "a"}]}}
    assert "lets_go" in names(ctx)
    ctx.clock.t += 4 * 3600                                         # the offer went stale
    assert "lets_go" not in names(ctx)


def test_the_exam_list_ignores_the_gates(ctx):
    assert names(ctx, chat.EXAM_TOOLS) == chat.EXAM_TOOLS & {t["function"]["name"] for t in chat.TOOLS}


@pytest.mark.asyncio
async def test_a_turn_without_the_state_does_not_send_the_gated_tools(ctx):
    ctx.openrouter = ScriptBrain({"content": "Hi."})
    await say(ctx, "hi there")
    sent = {t["function"]["name"] for t in ctx.openrouter.bodies[0]["tools"]}
    assert not (sent & STATE_ONLY) and "open_url" not in sent


@pytest.mark.asyncio
async def test_open_url_by_name_still_runs_the_move_and_logs_it(ctx, monkeypatch):
    from misty_engine.moves import basic
    got = []

    async def op(target):
        got.append(target)
    monkeypatch.setattr(chat, "_open", op)
    monkeypatch.setattr(basic, "_open", op, raising=False)
    r = await chat.run_tool(ctx, "open_url", {"url": "https://meet.google.com/abc-defg-hij"}, on_status=nothing, confirm=no)
    row = ctx.con.execute("SELECT name, params FROM moves_log ORDER BY at DESC LIMIT 1").fetchone()
    assert row["name"] == "open_url" and "meet.google.com" in row["params"]
    assert r["ok"] is True or "verified" in r
    bad = await chat.run_tool(ctx, "open_url", {"url": "http://x.com/a"}, on_status=nothing, confirm=no)
    assert bad["ok"] is False


def test_the_tool_pick_check_can_put_up_the_state_for_one_line(ctx):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "checks"))
    import toolpick as tp
    for need in ("lets_go", "answer_ask"):
        assert need not in names(ctx)
        undo = tp.arrange(ctx, need)
        assert need in names(ctx)
        undo()
        assert need not in names(ctx)
    with pytest.raises(ValueError):
        tp.arrange(ctx, "judge")


def test_slate_is_always_on_and_draw_only_in_a_lesson(ctx):
    assert "slate" in names(ctx) and "draw" not in names(ctx)
    assert "slate" in chat.SLATE_RULE and "slate" in chat.system_parts(ctx, "hi")[0]
    assert "slate" not in chat.system_parts(ctx, "hi", exam=True)[0]
