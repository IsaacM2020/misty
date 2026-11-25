"""The wrong-pick log (card B2b): a correction saves what he said on the turn before and the tool she picked, the engine
fills those in (not the model), the same tool saves nothing, and adding to his set backs up first and needs his command."""
import json

import pytest
import yaml

from misty_engine import chat, cli, wrong_picks
from test_chat import ScriptBrain, call, nothing, no, say


def turn(ctx, said, tool=None, args=None):
    """A finished earlier turn: what he said, and the one tool she called (or none)."""
    chat._save(ctx, "user", said)
    meta = {"calls": [{"name": tool, "args": args or {}, "result": "ok"}]} if tool else {"calls": []}
    chat._save(ctx, "assistant", "done", meta)


async def correct(ctx, wanted, note="no, not that"):
    chat._save(ctx, "user", note)                   # the correction itself is already saved when the tool runs
    return await chat.run_tool(ctx, "feedback", {"wanted": wanted, "note": note}, on_status=nothing, confirm=no)


@pytest.mark.asyncio
async def test_a_correction_saves_what_he_said_and_what_she_picked_from_the_turn_before(ctx):
    turn(ctx, "put on the drill", "open_fact", {"key": "drill"})
    r = await correct(ctx, "do", "no, I meant the drill app")
    assert r["saved"] is True
    row = wrong_picks.waiting(ctx.con)[0]
    assert (row["said"], row["picked_tool"], row["wanted_tool"], row["status"]) == ("put on the drill", "open_fact", "do", "new")
    assert "drill" in row["picked_args"] and "drill app" in row["note"]


@pytest.mark.asyncio
async def test_the_same_tool_as_wanted_saves_nothing(ctx):
    turn(ctx, "what's on tomorrow", "calendar", {"when": "tomorrow"})
    assert (await correct(ctx, "calendar"))["saved"] is False
    assert wrong_picks.waiting(ctx.con) == []


@pytest.mark.asyncio
async def test_none_as_the_pick_and_the_want(ctx):
    turn(ctx, "what is a prime number", "search", {"query": "prime number"})
    assert (await correct(ctx, "none", "just answer"))["saved"] is True
    turn(ctx, "thanks", None)
    assert (await correct(ctx, "none"))["saved"] is False                  # she did just answer
    assert wrong_picks.waiting(ctx.con)[0]["wanted_tool"] == "none"


@pytest.mark.asyncio
async def test_no_earlier_turn_and_a_missing_wanted_save_nothing(ctx):
    assert (await correct(ctx, "do"))["saved"] is False
    r = await chat.run_tool(ctx, "feedback", {"wanted": "  "}, on_status=nothing, confirm=no)
    assert r["ok"] is False or r.get("saved") is False


@pytest.mark.asyncio
async def test_a_secret_in_the_phrase_is_never_saved(ctx):
    turn(ctx, "remember my key sk-or-v1-" + "a1b2c3d4" * 8, "remember", {"key": "k"})
    assert (await correct(ctx, "none"))["saved"] is False and wrong_picks.waiting(ctx.con) == []


@pytest.mark.asyncio
async def test_ten_of_ten_scripted_corrections_save_the_right_row(ctx):
    cases = [(f"phrase number {i}", t, w) for i, (t, w) in enumerate(
        [("open_fact", "do"), ("calendar", "mail"), ("search", "recall"), ("none", "care"), ("remember", "care"),
         ("do", "none"), ("look", "draw"), ("work_on", "calendar"), ("recall", "class_search"), ("open_url", "open_fact")])]
    for said, picked, wanted in cases:
        turn(ctx, said, None if picked == "none" else picked)
        assert (await correct(ctx, wanted))["saved"] is True
    got = [(r["said"], r["picked_tool"], r["wanted_tool"]) for r in wrong_picks.waiting(ctx.con)]
    assert got == cases


@pytest.mark.asyncio
async def test_the_model_path_end_to_end_with_the_fake_brain(ctx):
    ctx.openrouter = ScriptBrain({"calls": [call("calendar", when="today")]}, {"content": "Nothing today."},
                                 {"calls": [call("feedback", wanted="mail", note="I meant my mail")]}, {"content": "My bad, checking mail."})
    await say(ctx, "anything new for me")
    out, _, _ = await say(ctx, "no, I meant my mail")
    assert out["actions"][0]["tool"] == "feedback"
    row = wrong_picks.waiting(ctx.con)[0]
    assert (row["said"], row["picked_tool"], row["wanted_tool"]) == ("anything new for me", "calendar", "mail")


def test_the_tool_count_is_unchanged_and_wanted_is_a_field_of_feedback(ctx):
    names = [t["function"]["name"] for t in chat.TOOLS]
    assert len(names) == 33 and names.count("feedback") == 1
    fb = next(t for t in chat.TOOLS if t["function"]["name"] == "feedback")
    assert "wanted" in fb["function"]["parameters"]["properties"]
    assert "wanted" not in chat.system_parts(ctx, "hi", exam=True)[0]


def test_add_writes_one_yaml_line_and_backs_up_first_and_drop_dismisses(ctx, tmp_path):
    for said, wanted in (("play the \"drill\" song", "do"), ("what's up with Zoë", "work_on")):
        ctx.con.execute("INSERT INTO wrong_picks(id, said, picked_tool, picked_args, wanted_tool, note, status, created_at) "
                        "VALUES (?,?,?,?,?,?, 'new', ?)", (f"wp_{wanted}", said, "search", "", wanted, "", 1.0))
    path, bak = tmp_path / "checks" / "toolpick.yaml", tmp_path / "backup"
    path.parent.mkdir()
    path.write_text("- {say: \"existing\", expect_tool: calc}")                  # no trailing newline on purpose
    r = wrong_picks.add_to_set(ctx.con, "wp_do", path, bak, now=1_792_000_000)
    assert r["ok"] and len(list(bak.glob("toolpick-*.yaml"))) == 1
    assert (bak / list(bak.iterdir())[0].name).read_text().strip() == '- {say: "existing", expect_tool: calc}'
    rows = yaml.safe_load(path.read_text())
    assert rows == [{"say": "existing", "expect_tool": "calc"}, {"say": 'play the "drill" song', "expect_tool": "do"}]
    assert ctx.con.execute("select status from wrong_picks where id='wp_do'").fetchone()[0] == "added"
    assert wrong_picks.add_to_set(ctx.con, "wp_do", path, bak)["ok"] is False        # not twice
    assert wrong_picks.dismiss(ctx.con, "wp_work_on")["ok"] and wrong_picks.waiting(ctx.con) == []


def test_the_cli_lists_adds_and_says_so_when_the_table_is_missing(ctx, misty_home, capsys):
    ctx.con.execute("INSERT INTO wrong_picks(id, said, picked_tool, picked_args, wanted_tool, note, status, created_at) "
                    "VALUES ('wp_x','open the thing','look','','do','', 'new', 1.0)")
    assert cli._picks(None, None) == 0
    assert "wp_x" in capsys.readouterr().out
    assert cli._picks("add", "wp_x") == 0
    assert "added to" in capsys.readouterr().out
    assert (misty_home.home / "checks" / "toolpick.yaml").exists()
    assert list((misty_home.home / "backup").glob("toolpick-*.yaml")) == []        # nothing existed to back up yet
