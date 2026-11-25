"""Hotkey voice replies sound like a person, and a long silence is a fresh start."""
from __future__ import annotations

from misty_engine import chat, ways


def _msg(ctx, role, text, at):
    with ctx.con:
        ctx.con.execute("INSERT INTO chat_messages(role, text, created_at) VALUES (?,?,?)", (role, text, at))


def test_voice_is_the_last_thing_in_the_static_block_and_only_when_spoken(ctx):
    spoken = chat.system_blocks(ctx, "hi", voice=True)
    typed = chat.system_blocks(ctx, "hi", voice=False)
    assert spoken[0].rstrip().endswith(chat.persona.render(ctx, chat.VOICE).rstrip()[-60:])
    assert spoken[0].index("Voice:") > spoken[0].index("When to open the Slate")      # after every rule block
    assert "Voice:" not in typed[0] + typed[1] and "Voice:" not in spoken[1]


def test_the_spoken_tail_line_is_there_only_for_voice(ctx):
    assert chat.VOICE_TAIL in chat.system_blocks(ctx, "hi", voice=True)[2]
    assert chat.VOICE_TAIL not in chat.system_blocks(ctx, "hi", voice=False)[2]


def test_a_stale_gap_drops_old_raw_messages_and_labels_the_summary(ctx):
    now = ctx.clock.now()
    _msg(ctx, "summary", "We talked about tides.", now - 9 * 3600)
    _msg(ctx, "user", "old question", now - 8 * 3600)
    _msg(ctx, "assistant", "old answer", now - 8 * 3600 + 5)
    _, history, _, _ = chat.prefix(ctx, "hello", voice=True)
    assert history == []
    parts = chat.system_blocks(ctx, "hello", voice=True)
    user = parts[1]
    assert "old, only use if he brings it up" in user and "We talked about tides." in user
    assert "His last message was 8 hours ago." in parts[2] and "fresh start" in parts[2]


def test_a_recent_chat_replays_and_a_short_gap_has_no_fresh_start_line(ctx):
    now = ctx.clock.now()
    _msg(ctx, "user", "earlier", now - 3600)
    _msg(ctx, "assistant", "reply", now - 3590)
    _, history, _, _ = chat.prefix(ctx, "hello", voice=True)
    assert history
    tail = chat.system_blocks(ctx, "hello", voice=True)[2]
    assert "His last message was 1 hour ago." in tail and "fresh start" not in tail


def test_no_gap_line_within_half_an_hour(ctx):
    _msg(ctx, "user", "earlier", ctx.clock.now() - 600)
    assert "His last message" not in chat.system_blocks(ctx, "hello", voice=True)[2]


def test_the_name_line_asks_for_sparing_use_and_keeps_the_never_real_name_rule(ctx):
    ways.set_way(ctx, "address", "Call him boss.", "address", "boss")
    line = ways._name_line(ctx.con)
    assert 'call him "boss"' in line and "never by his real name" in line
    assert "one reply in four" in line and "never after a comma at the end" in line
