"""Ways on every surface (step B, card B5): defaults seed once, the brief / follow judge / class ask prompts carry his
ways, the fixed "Good morning" order is gone, and the ask line uses the name he picked."""
import json

import pytest

from misty_engine import brief, gate, ways
from test_brief import JSON_OK  # noqa: F401  (same fake model reply)
from test_chat import ScriptBrain


def test_defaults_seed_an_empty_table_once_and_a_dropped_default_stays_gone(ctx):
    assert ways.seed_defaults(ctx) == 3
    rows = ways.active(ctx.con)
    assert {r["key"] for r in rows} == {"character", "brief greeting", "length"} and {r["origin"] for r in rows} == {"default"}
    assert ways.seed_defaults(ctx) == 0
    ways.drop(ctx, "brief greeting")
    assert ways.seed_defaults(ctx) == 0 and "brief greeting" not in {r["key"] for r in ways.active(ctx.con)}


def test_the_character_default_names_no_film_person_or_actor(ctx):
    ways.seed_defaults(ctx)
    text = ways.get(ctx.con, "character")["text"].lower()
    for banned in ("edith", "e.d.i.t.h", "spider", "jarvis", "movie", "film", "actor", "voice"):
        assert banned not in text


def test_a_missing_defaults_file_adds_nothing(ctx, tmp_path):
    assert ways.seed_defaults(ctx, tmp_path / "nope.toml") == 0


def test_style_note_puts_bans_and_names_first_and_never_cuts_a_way_in_half(ctx):
    ways.set_way(ctx, "tone", "Dry and understated.", "tone")
    ways.set_way(ctx, "no good morning", "Never open with 'good morning'.", "never_say")
    ways.set_way(ctx, "address", "Call him boss.", "address", "boss")
    n = ways.style_note(ctx.con)
    assert n.index("Never open") < n.index("Call him boss") < n.index("Dry and")
    for i in range(10):
        ways.set_way(ctx, f"habit {i}", "Be extremely long about this one thing, " + "x" * 120 + ".", "habit")
    n = ways.style_note(ctx.con)
    assert len(n) <= ways.STYLE_MAX and "Never open" in n and n.endswith(".")


def test_with_style_is_unchanged_with_no_ways(ctx):
    assert ways.with_style(ctx.con, "SYSTEM") == "SYSTEM"
    ways.set_way(ctx, "address", "Call him boss.", "address", "boss")
    assert ways.with_style(ctx.con, "SYSTEM").startswith("SYSTEM\n\n") and "Call him boss." in ways.with_style(ctx.con, "SYSTEM")


@pytest.mark.asyncio
async def test_the_brief_prompt_has_his_ways_and_no_fixed_good_morning(ctx):
    ways.set_way(ctx, "no good morning", "Never open with 'good morning'.", "never_say")
    ctx.openrouter = ScriptBrain({"content": json.dumps({"say": "Physics is at eight.", "task": "x", "open": []})})
    await brief.compose(ctx, {"facts": {"day": "Sunday"}, "openable": []})
    system = ctx.openrouter.bodies[0]["messages"][0]["content"]
    assert 'Start with "Good morning."' not in system and "Never open with 'good morning'." in system


def test_the_ask_line_uses_the_name_he_asked_for_and_is_plain_without_one(ctx):
    m = gate.Moment(text="x", topic="follow", topic_text="the Man City match", urgency="later")
    assert gate._ask_text(m) == "I've got something on the Man City match. Want to hear it?"
    assert gate._ask_text(m, "boss") == "I've got something on the Man City match, boss. Want to hear it?"


def test_an_address_way_is_also_said_in_plain_words_in_the_chat_block_and_the_style_note(ctx):
    assert ways.block(ctx.con) == "" and ways.style_note(ctx.con) == ""
    ways.set_way(ctx, "address", "Call him boss.", "address", "boss")
    line = ways._name_line(ctx.con)
    assert 'call him "boss", never by his real name' in line and 'never after a comma at the end' in line
    assert ways.block(ctx.con).endswith(line) and line in ways.style_note(ctx.con)
    ways.drop(ctx, "address")
    ways.set_way(ctx, "tone", "Dry.", "tone")
    assert "never by his real name" not in ways.block(ctx.con)


def test_banned_phrases_come_from_quoted_words_in_never_say_ways(ctx):
    ways.set_way(ctx, "no good morning", "Never open with 'good morning'.", "never_say")
    ways.set_way(ctx, "tone", "Say 'mate' a lot.", "tone")
    assert ways.banned_phrases(ctx.con) == ["good morning"]
    assert ways.broken_by(ctx.con, "Good  Morning, boss.") == "good morning" and ways.broken_by(ctx.con, "Morning, boss.") is None


@pytest.mark.asyncio
async def test_the_brief_is_asked_again_once_when_a_banned_phrase_slips_through(ctx):
    ways.set_way(ctx, "no good morning", "Never open with 'good morning'.", "never_say")
    ctx.openrouter = ScriptBrain({"content": json.dumps({"say": "Good morning. Physics is at eight.", "task": "x", "open": []})},
                                 {"content": json.dumps({"say": "Physics is at eight.", "task": "x", "open": []})})
    out = await brief.compose(ctx, {"facts": {"day": "Sunday"}, "openable": []})
    assert out["say"] == "Physics is at eight." and len(ctx.openrouter.bodies) == 2
    assert "good morning" in ctx.openrouter.bodies[1]["messages"][-1]["content"]
    ctx.openrouter = ScriptBrain({"content": json.dumps({"say": "Good morning.", "task": "x", "open": []})},
                                 {"content": json.dumps({"say": "Good morning again.", "task": "x", "open": []})})
    assert (await brief.compose(ctx, {"facts": {}, "openable": []}))["say"] == "Good morning again."   # only one retry
    assert len(ctx.openrouter.bodies) == 2


@pytest.mark.asyncio
async def test_ways_list_is_the_same_rows_as_the_prompt_and_carries_the_default_tag(ctx):
    """Card B7: what the Radar tab lists is what she reads in her own prompt, with kind and origin."""
    from misty_engine import chat
    from misty_engine.api import build_methods
    ways.seed_defaults(ctx)
    ways.set_way(ctx, "address", "Call him boss.", "address", "boss")
    r = await build_methods(ctx)["ways.list"]({})
    assert {w["key"]: w["origin"] for w in r["ways"]} == {"character": "default", "brief greeting": "default", "length": "default", "address": "chat"}
    assert all({"id", "key", "kind", "text", "origin"} <= set(w) for w in r["ways"])
    fixed, _ = chat.system_parts(ctx, "x")
    assert all(w["text"] in fixed for w in r["ways"])
    ways.drop(ctx, "length")
    assert "length" not in {w["key"] for w in (await build_methods(ctx)["ways.list"]({}))["ways"]}
