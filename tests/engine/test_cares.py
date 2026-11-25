"""Cares (Season 3, Day 3): what Isaac says matters to him is saved by chat, changed by key, dropped on request, shown to
the model every turn, and kept out of the Season 2 exam."""
import pytest

from misty_engine import cares, chat
from misty_engine.errors import Fatal
from test_chat import ScriptBrain, call, say, sys_text


def test_a_care_is_added_then_the_same_key_changes_it(ctx):
    r = cares.set_care(ctx, "Cricket", "Isaac loves cricket, India and the IPL", "interest", "high")
    assert (r["action"], r["key"], r["weight"], r["was"]) == ("added", "cricket", 3, None)
    r = cares.set_care(ctx, "cricket", "Cricket matters less now", "interest", "low")
    assert r["action"] == "updated" and r["weight"] == 1 and "IPL" in r["was"]
    rows = cares.active(ctx.con)
    assert len(rows) == 1 and rows[0]["weight"] == 1
    assert ctx.con.execute("select count(*) from cares where status='replaced'").fetchone()[0] == 1


def test_a_plural_or_near_key_is_the_same_care(ctx):
    cares.set_care(ctx, "physics tutor", "Prashant is the tutor he cares about", "person", "normal")
    assert cares.set_care(ctx, "Physics tutors", "still cares about his tutor", "person", "high")["action"] == "updated"
    assert len(cares.active(ctx.con)) == 1


def test_never_is_kept_as_a_care_with_weight_zero(ctx):
    cares.set_care(ctx, "football", "does not want to hear about football", "interest", "never")
    assert cares.active(ctx.con)[0]["weight"] == 0
    assert "never bring it up" in cares.block(ctx.con)


def test_unknown_kind_and_level_fall_back_to_safe_defaults(ctx):
    r = cares.set_care(ctx, "chess", "wants to get better at chess", "hobby", "very")
    assert (r["kind"], r["weight"]) == ("other", 2)


def test_drop_marks_it_dropped_and_missing_is_zero(ctx):
    cares.set_care(ctx, "arjun", "Arjun is a friend who matters", "person", "high")
    assert cares.drop(ctx, "Arjun")["dropped"] == 1 and cares.active(ctx.con) == []
    assert ctx.con.execute("select status from cares").fetchone()[0] == "dropped"
    assert cares.drop(ctx, "nobody")["dropped"] == 0


def test_a_password_is_never_saved_as_a_care(ctx):
    with pytest.raises(Fatal):
        cares.set_care(ctx, "wifi", "my password is hunter2hunter2 sk-abcdefghijklmnopqrstuvwxyz0123456789", "other", "high")
    assert cares.active(ctx.con) == []


def test_empty_key_or_text_is_refused(ctx):
    with pytest.raises(Fatal):
        cares.set_care(ctx, "  ", "something", "goal", "high")
    with pytest.raises(Fatal):
        cares.set_care(ctx, "x", "   ", "goal", "high")


@pytest.mark.asyncio
async def test_chat_care_tool_saves_and_the_next_message_sees_it(ctx):
    ctx.openrouter = ScriptBrain(
        {"calls": [call("care", key="ib math hl", kind="goal", text="Isaac wants a 7 in IB Math HL", level="high")]},
        {"content": "Got it, a 7 in Math HL is a big goal."},
        {"content": "ok"})
    out, text, _ = await say(ctx, "a 7 in IB Math HL is my big goal")
    assert out["actions"][0]["tool"] == "care" and out["actions"][0]["ok"]
    assert cares.active(ctx.con)[0]["key"] == "ib math hl"
    await say(ctx, "what do you care about")
    assert "ib math hl (goal, matters a lot): Isaac wants a 7 in IB Math HL" in sys_text(ctx.openrouter.bodies[-1]["messages"][0])


@pytest.mark.asyncio
async def test_chat_uncare_that_finds_nothing_says_so(ctx):
    ctx.openrouter = ScriptBrain({"calls": [call("uncare", key="ghost")]}, {"content": "You had nothing like that."})
    out, _, _ = await say(ctx, "stop caring about ghosts")
    assert out["actions"][0]["ok"] is False


def test_the_exam_prompt_has_no_cares_block(ctx):
    ctx.settings.set("user_name", "Isaac")             # the prompt now fills his name in from settings
    cares.set_care(ctx, "cricket", "loves cricket", "interest", "high")
    fixed, _ = chat.system_parts(ctx, "hi", exam=True)
    assert "call care" not in fixed and "What Isaac cares about" not in fixed
    fixed, _ = chat.system_parts(ctx, "hi")
    assert "call care" in fixed and "What Isaac cares about" in fixed and "cricket" in fixed


@pytest.mark.asyncio
async def test_cares_list_rpc_is_what_the_radar_tab_shows(ctx):
    from misty_engine.api import build_methods
    cares.set_care(ctx, "cricket", "loves cricket", "interest", "high")
    cares.set_care(ctx, "football", "no football", "interest", "never")
    cares.set_care(ctx, "arjun", "a friend", "person", "normal")
    cares.drop(ctx, "arjun")
    out = await build_methods(ctx)["cares.list"]({})
    assert [(c["key"], c["kind"], c["weight"], c["level"]) for c in out["cares"]] == [
        ("cricket", "interest", 3, "matters a lot"), ("football", "interest", 0, "never bring it up")]
