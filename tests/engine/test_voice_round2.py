"""Voice round 2: the calendar knows the time, the name is rationed, a finished lesson stays finished, no empty promises."""
from __future__ import annotations

import json

import pytest

from misty_engine import chat, tutor, ways
from test_chat import ScriptBrain, call, nothing, no


def _msg(ctx, role, text, at, meta=None):
    with ctx.con:
        ctx.con.execute("INSERT INTO chat_messages(role, text, meta, created_at) VALUES (?,?,?,?)",
                        (role, text, json.dumps(meta) if meta else None, at))


# ---- 4: wording
def test_prompt_says_never_promise_later_and_lead_with_what_is_ahead(ctx):
    for text in (chat.VOICE, chat.VOICE_TAIL):
        assert "Never promise to do something later" in text
    assert "lead with what is still ahead" in chat.VOICE.lower() and '"done"' in chat.VOICE
    assert "one concrete proposal now" in chat.VOICE


# ---- 3: lesson state
def test_ends_lesson_phrases():
    for t in ("just give me the answer", "Nothing else, please", "No graph, just the answer", "skip the board"):
        assert tutor.ends_lesson(t), t
        assert not tutor.continues_lesson(t), t
    assert not tutor.ends_lesson("is it 5 times 10 to the 4?")


def test_a_lesson_marked_over_is_not_resumed(ctx):
    now = ctx.clock.now()
    board = {"scene": {"steps": []}, "asks": [1], "upto": 0}
    _msg(ctx, "assistant", "here is the board", now - 60, {"calls": [{"name": "solve", "args": {}, "result": "{}"}], "lesson": board})
    assert chat._tutoring(ctx) and chat._lesson(ctx) is not None
    _msg(ctx, "assistant", "The answer is 4.", now - 30, {"lesson_over": True})
    assert not chat._tutoring(ctx) and chat._lesson(ctx) is None


def test_a_lesson_older_than_half_an_hour_is_not_resumed(ctx):
    now = ctx.clock.now()
    board = {"scene": {"steps": []}, "asks": [1], "upto": 0}
    _msg(ctx, "assistant", "board", now - chat.TUTORING_SECONDS - 60, {"calls": [{"name": "solve", "args": {}, "result": "{}"}], "lesson": board})
    assert not chat._tutoring(ctx) and chat._lesson(ctx) is None


@pytest.mark.asyncio
async def test_judge_other_ends_the_lesson_and_tells_her_not_to_go_back(ctx):
    lesson = {"scene": {"steps": []}, "asks": [0, 1], "upto": 0}
    res = await chat._judge(ctx, lesson, "other")
    assert res["lesson_over"] is True and "do NOT bring him back" in res["note"]


@pytest.mark.asyncio
async def test_just_give_me_the_answer_ends_the_lesson_in_a_real_turn(ctx):
    now = ctx.clock.now()
    board = {"scene": {"steps": []}, "asks": [0], "upto": 0}
    _msg(ctx, "assistant", "board up", now - 60, {"calls": [{"name": "solve", "args": {}, "result": "{}"}], "lesson": board})
    ctx.openrouter = ScriptBrain({"content": "It is about 0.043."})
    out = await chat.send(ctx, "just give me the answer", on_delta=nothing, on_status=nothing, confirm=no)
    assert out["reply"].startswith("It is about")
    last = ctx.con.execute("SELECT meta FROM chat_messages WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()
    assert json.loads(last["meta"]).get("lesson_over") is True
    assert not chat._tutoring(ctx)


@pytest.mark.asyncio
async def test_compaction_drops_a_summary_older_than_a_fresh_start(ctx):
    now = ctx.clock.now()
    _msg(ctx, "summary", "Old functions paper Q1 lesson.", now - (chat.FRESH_START_HOURS + 1) * 3600, {"through": 0})
    for i in range(chat.KEEP + 4):
        chat._save(ctx, "user" if i % 2 == 0 else "assistant", f"message {i}")
    ctx.openrouter = ScriptBrain({"content": "New summary."})
    assert await chat.compact(ctx) is True
    sent = ctx.openrouter.bodies[0]["messages"][1]["content"]
    assert "functions paper" not in sent and "(none)" in sent


@pytest.mark.asyncio
async def test_compaction_keeps_a_recent_summary(ctx):
    now = ctx.clock.now()
    _msg(ctx, "summary", "Physics test on the 30th.", now - 3600, {"through": 0})
    for i in range(chat.KEEP + 4):
        chat._save(ctx, "user" if i % 2 == 0 else "assistant", f"message {i}")
    ctx.openrouter = ScriptBrain({"content": "New summary."})
    await chat.compact(ctx)
    assert "Physics test on the 30th." in ctx.openrouter.bodies[0]["messages"][1]["content"]


# ---- 2: the name, in a real spoken turn
@pytest.mark.asyncio
async def test_the_name_is_stripped_when_used_recently_and_kept_otherwise(ctx):
    ways.set_way(ctx, "address", "Call him boss.", "address", "boss")
    _msg(ctx, "assistant", "Okay, boss.", ctx.clock.now() - 100)
    ctx.openrouter = ScriptBrain({"content": "Boss, not much, boss."})
    out = await chat.send(ctx, "what's up", on_delta=nothing, on_status=nothing, confirm=no, voice=True)
    assert "boss" not in out["reply"].lower() and out["reply"] == "Not much."
    ctx.openrouter = ScriptBrain({"content": "Yeah, boss, sure."})              # "Okay, boss." is still within the last 3 replies
    out = await chat.send(ctx, "ok", on_delta=nothing, on_status=nothing, confirm=no, voice=True)
    assert "boss" not in out["reply"].lower()
    for _ in range(2):
        _msg(ctx, "assistant", "fine", ctx.clock.now())
    ctx.openrouter = ScriptBrain({"content": "Yeah, boss, sure."})
    out = await chat.send(ctx, "ok again", on_delta=nothing, on_status=nothing, confirm=no, voice=True)
    assert out["reply"] == "Yeah, boss, sure."


# ---- 1: the tool result itself
@pytest.mark.asyncio
async def test_the_calendar_tool_labels_events_against_the_clock(ctx, monkeypatch):
    from datetime import datetime
    from misty_engine import calendar_now
    from misty_engine.sources import google_auth
    sgt = calendar_now.SGT
    evs = [{"start": datetime.fromtimestamp(ctx.clock.now() - 9000, sgt), "end": datetime.fromtimestamp(ctx.clock.now() - 3600, sgt),
            "all_day": False, "title": "Prashant Class", "location": "", "link": None, "calendar": ""},
           {"start": datetime.fromtimestamp(ctx.clock.now() + 3 * 3600, sgt), "end": datetime.fromtimestamp(ctx.clock.now() + 4 * 3600, sgt),
            "all_day": False, "title": "English Class", "location": "", "link": None, "calendar": ""}]
    monkeypatch.setattr(calendar_now, "events_between", lambda svc, start, days: evs)
    monkeypatch.setattr(google_auth, "service", lambda name: object())
    ctx.net.online = True
    res = await chat._calendar(ctx, {"when": "today"}, nothing)
    assert "done, ended 1h ago" in res["events"][0] and "in 3h" in res["events"][1]
