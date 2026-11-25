"""V7: spoken replies are at most 2 short sentences, about 25 words, unless he asks to go deeper. Text replies do not change."""
from __future__ import annotations

from misty_engine import chat


def test_the_voice_block_caps_a_reply_at_two_short_sentences_and_about_25_words():
    v = chat.VOICE
    assert "1 or 2 short sentences" in v and "about 25 words" in v
    assert "1 to 3 short sentences" not in v            # the old, looser rule is gone


def test_the_voice_block_lets_him_ask_for_more():
    v = chat.VOICE
    assert "explain more" in v and "go deeper" in v


def test_the_voice_block_keeps_the_v2_rules():
    v = chat.VOICE
    assert "answer comes first" in v and "Never a list" in v and "Do not read out your working" in v


def test_the_cap_leaves_room_for_a_tool_call_but_is_far_under_the_text_cap():
    # 25 words is about 40 tokens; a tool call's arguments count against the same cap, so there is headroom, not 300
    assert 120 <= chat.VOICE_MAX_TOKENS <= 220
    assert chat.VOICE_MAX_TOKENS < chat.MAX_TOKENS


def test_voice_turns_use_the_voice_cap_and_text_turns_do_not(ctx):
    assert chat._chat_body is not None
    import inspect
    src = inspect.getsource(chat)
    assert "VOICE_MAX_TOKENS if voice else MAX_TOKENS" in src


def test_the_text_prompt_does_not_carry_the_voice_rule(ctx):
    a = chat.system_parts(ctx, "explain price elasticity", voice=False, exam=False)
    assert "25 words" not in a[0] + a[1] and "Voice:" not in a[0] + a[1]
    v = chat.system_parts(ctx, "explain price elasticity", voice=True, exam=False)
    assert "25 words" in v[0] + v[1]


def test_voice_calls_tools_with_no_text_before_and_never_narrates():
    "empty text part" in chat.VOICE and "Never narrate" in chat.VOICE
    assert "no words before it" in chat.VOICE_TAIL and "reasoning" in chat.VOICE_TAIL
    assert "very first act, before any text" in chat.BRIEF_RULE


def test_voice_lists_in_prose_are_still_lists_so_one_point_or_the_slate():
    v = chat.VOICE
    assert "secretly a list" in v and "single most useful point" in v and "open the Slate" in v
    assert "ONE sentence" in chat.VOICE_TAIL


def test_the_name_goes_first_never_last_and_the_tail_stays_generic():
    assert "very start" in chat.VOICE and "never in a one-sentence reply" in chat.VOICE
    assert "FIRST word" in chat.VOICE_TAIL and "<name>" in chat.VOICE_TAIL and "boss" not in chat.VOICE_TAIL.lower()


def test_a_spoken_brief_forces_work_on_before_any_text():
    for q in ("what's up", "Brief me", "what's my day?", "what should I work on"):
        assert chat.BRIEF_ASK.match(q), q
    for q in ("what's up with the exam", "explain brief me", "what's 2 plus 2"):
        assert not chat.BRIEF_ASK.match(q), q
    import inspect
    src = inspect.getsource(chat.send)
    assert "rnd == 0 and voice and not exam and BRIEF_ASK.match(text)" in src
