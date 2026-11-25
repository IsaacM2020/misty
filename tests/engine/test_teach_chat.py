"""E7: the lesson inside chat.send. His answer is graded in code before the model runs; the model only words the result."""
import asyncio
import json

import pytest

from misty_engine import chat
from misty_engine.teach import live
from test_chat import ScriptBrain, call, nothing, no, say
from test_teach_live import PASSAGE, put_plan, src, topic, kinds  # noqa: F401  (fixtures)


def lesson_text(brain):
    """The tool result the model was given in its first round."""
    for m in brain.bodies[0]["messages"]:
        if m.get("role") == "tool" and m.get("tool_call_id") == "call_teach":
            return json.loads(m["content"])
    return None


def system_text(body):
    c = body["messages"][0]["content"]
    return "".join(x["text"] for x in c) if isinstance(c, list) else c


@pytest.mark.asyncio
async def test_a_live_lesson_grades_his_answer_before_the_model_and_only_words_it(fctx, topic):
    await live.start(fctx, topic)
    brain = ScriptBrain({"content": "Right. Next idea: say plain 1. Question 1?"})
    fctx.openrouter.post = brain.post
    out, text, _ = await say(fctx, "it is ans0")
    res = lesson_text(brain)
    assert res["do"][0]["verdict"] == "right" and res["do"][1]["explain"] == "say plain 1"
    assert kinds(fctx) == ["right_helped"] and live.current(fctx)["step_no"] == 1
    b = brain.bodies[0]
    assert b["tool_choice"] == "none" and "A lesson is running" in json.dumps(b["messages"])
    assert [a["tool"] for a in out["actions"]] == ["teach"]
    assert "ans1" not in json.dumps(b["messages"])                    # the next answer is nowhere in the prompt


@pytest.mark.asyncio
async def test_a_wrong_answer_is_graded_wrong_by_code_not_by_the_model(fctx, topic):
    await live.start(fctx, topic)
    brain = ScriptBrain({"content": "Not quite. Picture it."})
    fctx.openrouter.post = brain.post
    await say(fctx, "banana")
    res = lesson_text(brain)
    assert res["do"][0]["verdict"] == "wrong" and res["do"][1]["way"] == "picture" and kinds(fctx) == ["wrong"]


@pytest.mark.asyncio
async def test_help_words_inside_a_lesson_do_not_start_look_and_solve(fctx, topic):
    await live.start(fctx, topic)
    brain = ScriptBrain({"content": "Here is a hint."})
    fctx.openrouter.post = brain.post
    out, _, _ = await say(fctx, "I'm stuck, can you help me solve this")
    assert [a["tool"] for a in out["actions"]] == ["teach"]           # no look, no solve preflight
    assert lesson_text(brain)["do"][0] == {"hint": "hint one"}


@pytest.mark.asyncio
async def test_a_side_question_keeps_tools_open_and_comes_back(fctx, topic):
    await live.start(fctx, topic)
    brain = ScriptBrain({"content": "Because it saves energy. Question 0?"})
    fctx.openrouter.post = brain.post
    await say(fctx, "why do runners do that?")
    res = lesson_text(brain)
    assert res["do"][0] == {"side_question": True} and res["do"][-1] == {"then_ask": "Question 0?"}
    assert brain.bodies[0]["tool_choice"] == "auto" and kinds(fctx) == []


@pytest.mark.asyncio
async def test_without_a_lesson_chat_is_untouched_and_the_rule_is_absent(fctx, topic):
    brain = ScriptBrain({"content": "Hello."})
    fctx.openrouter.post = brain.post
    out, _, _ = await say(fctx, "ans0")
    assert out["actions"] == [] and "A lesson is running" not in system_text(brain.bodies[0])
    assert "teach with action start" in system_text(brain.bodies[0])  # the routing rule is always there


@pytest.mark.asyncio
async def test_the_exam_prompt_never_sees_teaching(fctx, topic):
    await live.start(fctx, topic)
    fixed, changing = chat.system_parts(fctx, "x", exam=True)
    assert "A lesson is running" not in fixed + changing and "teach with action start" not in fixed + changing


@pytest.mark.asyncio
async def test_the_model_can_start_a_lesson_with_the_tool(fctx, topic):
    brain = ScriptBrain({"calls": [call("teach", action="start", topic="negative split")]}, {"content": "Okay. say plain 0. Question 0?"})
    fctx.openrouter.post = brain.post
    out, _, _ = await say(fctx, "teach me negative splits")
    assert live.current(fctx) is not None and [a["tool"] for a in out["actions"]] == ["teach"]
    tool_msgs = [m for m in brain.bodies[1]["messages"] if m.get("role") == "tool"]
    assert "say plain 0" in tool_msgs[-1]["content"] and "ans0" not in tool_msgs[-1]["content"]


@pytest.mark.asyncio
async def test_text_she_read_cannot_start_a_lesson(fctx, topic):
    r = await chat.run_tool(fctx, "teach", {"action": "start", "topic": "negative split"}, on_status=nothing, confirm=no, source="text")
    assert r["ok"] is False and live.current(fctx) is None


@pytest.mark.asyncio
async def test_teach_pending_names_the_lesson(fctx, topic):
    assert chat.teach_pending(fctx) is None
    await live.start(fctx, topic)
    assert chat.teach_pending(fctx) == "lesson"


@pytest.mark.asyncio
async def test_a_way_with_a_board_draws_it_by_code(fctx, topic, src):
    from misty_engine.teach import plan as tplan
    from test_teach_plan import GOOD_BOARD
    board = tplan._board(GOOD_BOARD)
    assert board
    pl = json.loads(fctx.con.execute("select plan_json from teach_plans").fetchone()[0])
    pl["steps"][0]["explain"][0]["board"] = board
    fctx.con.execute("update teach_plans set plan_json=?", (json.dumps(pl),))
    seen = []

    async def notify(name, payload):
        seen.append((name, payload))
    fctx.notify = notify
    brain = ScriptBrain({"calls": [call("teach", action="start", topic="negative split")]}, {"content": "Look at this."})
    fctx.openrouter.post = brain.post
    await say(fctx, "teach me negative splits")
    assert [n for n, _ in seen] == ["slate.show"] and seen[0][1]["blocks"]
    tool_msgs = [m for m in brain.bodies[1]["messages"] if m.get("role") == "tool"]
    assert "_board" not in tool_msgs[-1]["content"] and "scene" not in tool_msgs[-1]["content"]      # the model gets no board data


@pytest.mark.asyncio
async def test_the_model_is_told_the_picture_is_narrated_only_when_one_was_drawn(fctx, topic, src):
    from misty_engine.teach import plan as tplan
    from test_teach_plan import GOOD_BOARD
    # no board: no flag
    res = {"ok": True, "do": [{"explain": "x"}], "_board": None}
    await chat._teach_draw(fctx, res)
    assert "narrated" not in res
    board = tplan._board(GOOD_BOARD)
    seen = []

    async def notify(name, payload):
        seen.append(name)
    fctx.notify = notify
    res = {"ok": True, "do": [], "_board": board}
    await chat._teach_draw(fctx, res)
    assert res["narrated"] is True and "_board" not in res and seen == ["slate.show"]


def test_the_lesson_rule_says_the_window_reads_the_picture_and_wrong_is_one_sentence():
    from misty_engine.teach import live as L
    assert "narrated" in L.TEACH_RULE and "do not read the picture" in L.TEACH_RULE
    assert "one sentence" in L.TEACH_RULE
