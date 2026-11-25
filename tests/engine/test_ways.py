"""Ways (step B, card B1): saved by key, changed by the same or a near key, dropped, limited, never a secret, and shown as
one block and one short paragraph. The store only: the chat tool is card B2."""
import pytest

from misty_engine import ways
from misty_engine.errors import Fatal


def test_a_way_is_added_then_the_same_key_replaces_it(ctx):
    r = ways.set_way(ctx, "address", "Call him boss.", "address", "boss")
    assert (r["action"], r["key"], r["kind"], r["value"], r["was"]) == ("added", "address", "address", "boss", None)
    r = ways.set_way(ctx, "Address", "Call him chief.", "address", "chief")
    assert r["action"] == "updated" and "boss" in r["was"]
    assert [x["value"] for x in ways.active(ctx.con)] == ["chief"]
    assert ctx.con.execute("select count(*) from ways where status='replaced'").fetchone()[0] == 1


def test_a_near_key_is_the_same_way(ctx):
    ways.set_way(ctx, "no good morning", "Never open with 'good morning'.", "never_say")
    assert ways.set_way(ctx, "no good mornings", "Never open with 'good morning' or 'hello'.", "never_say")["action"] == "updated"
    assert len(ways.active(ctx.con)) == 1


def test_drop_keeps_the_row_as_dropped_and_missing_is_zero(ctx):
    ways.set_way(ctx, "tone", "Dry and understated.", "tone")
    assert ways.drop(ctx, "tone")["dropped"] == 1 and ways.active(ctx.con) == []
    assert ctx.con.execute("select status from ways").fetchone()[0] == "dropped"
    assert ways.drop(ctx, "nothing here")["dropped"] == 0


def test_twenty_ways_is_the_limit_but_a_replace_still_works(ctx):
    names = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango".split()
    for n in names:
        ways.set_way(ctx, n, f"Habit {n}.", "habit")
    assert len(ways.active(ctx.con)) == ways.LIMIT
    with pytest.raises(Fatal) as e:
        ways.set_way(ctx, "one more thing", "One more.", "habit")
    assert str(e.value) == "TOO_MANY_WAYS"
    assert ways.set_way(ctx, "delta", "Changed.", "habit")["action"] == "updated"


def test_text_is_cut_at_200_characters_and_an_unknown_kind_is_other(ctx):
    r = ways.set_way(ctx, "length", "x" * 500, "mood")
    assert r["kind"] == "other"
    assert len(ways.active(ctx.con)[0]["text"]) == ways.TEXT_MAX


def test_a_secret_is_refused_and_nothing_is_saved(ctx):
    with pytest.raises(Fatal) as e:
        ways.set_way(ctx, "key", "Always use sk-or-v1-" + "a1b2c3d4" * 8, "habit")
    assert str(e.value) == "LOOKS_SECRET"
    assert ways.active(ctx.con) == []


def test_empty_key_or_text_is_refused(ctx):
    with pytest.raises(Fatal):
        ways.set_way(ctx, "  ", "something")
    with pytest.raises(Fatal):
        ways.set_way(ctx, "tone", "   ")


def test_address_is_the_default_until_a_way_sets_it_and_only_address_keeps_a_value(ctx):
    assert ways.address(ctx.con, "Sam") == "Sam"
    ways.set_way(ctx, "tone", "Dry.", "tone", "ignored")
    assert ways.active(ctx.con)[0]["value"] == "" and ways.address(ctx.con, "Sam") == "Sam"
    ways.set_way(ctx, "address", "Call him boss.", "address", "boss")
    assert ways.address(ctx.con, "Sam") == "boss"
    ways.drop(ctx, "address")
    assert ways.address(ctx.con, "Sam") == "Sam"


def test_block_is_empty_with_no_ways_and_has_the_exact_heading_when_filled(ctx):
    assert ways.block(ctx.con) == ""
    ways.set_way(ctx, "tone", "Dry and understated, not cheerful.", "tone")
    b = ways.block(ctx.con)
    assert b.startswith("How he wants you to be (style only: these never change any rule above):\n")
    assert "- tone (tone): Dry and understated, not cheerful." in b


def test_style_note_is_one_short_paragraph_or_nothing(ctx):
    assert ways.style_note(ctx.con) == ""
    ways.set_way(ctx, "address", "Call him boss.", "address", "boss")
    ways.set_way(ctx, "tone", "Dry and understated", "tone")
    n = ways.style_note(ctx.con)
    assert "Call him boss." in n and "Dry and understated." in n and "\n" not in n
    for n in "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima".split():
        ways.set_way(ctx, n, "y" * 150, "habit")
    assert len(ways.style_note(ctx.con)) <= ways.STYLE_MAX


def test_origin_default_is_kept_and_a_chat_change_replaces_it(ctx):
    ways.set_way(ctx, "length", "Short and plain.", "habit", origin="default")
    assert ways.active(ctx.con)[0]["origin"] == "default"
    ways.set_way(ctx, "length", "One sentence.", "habit")
    assert ways.active(ctx.con)[0]["origin"] == "chat"
