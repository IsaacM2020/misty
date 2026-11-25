"""Her character and the spoken-style rule live in one file and reach every prompt that writes a line she says."""
from misty_engine import brief, chat, follows, persona


def test_spoken_rule_is_in_every_prompt_that_writes_speech():
    for prompt in (chat.VOICE, follows.JUDGE_PROMPT, brief.PROMPT):
        assert persona.SPOKEN in prompt
    assert "Only what the items say" in follows.JUDGE_PROMPT and "{mode}" in follows.JUDGE_PROMPT
    assert "Use ONLY the facts below" in brief.PROMPT and '{"say": "..."' in brief.PROMPT


def test_character_is_in_chat_but_never_in_the_exam(ctx):
    fixed, _ = chat.system_parts(ctx, "hi")
    character = persona.render(ctx, persona.CHARACTER)
    assert character in fixed and fixed.index(character) > fixed.index(chat.BRIEF_RULE.rstrip())
    exam_fixed, exam_changing = chat.system_parts(ctx, "hi", exam=True)
    assert character not in exam_fixed + exam_changing and persona.SPOKEN not in exam_fixed + exam_changing
    assert exam_fixed.startswith(persona.render(ctx, chat.SYSTEM) + "\n\nToday is ")


def test_no_stock_assistant_phrases_are_taught():
    assert "I'd be happy to" in persona.CHARACTER and "Never nag" in persona.CHARACTER   # named so she avoids them


# ---- card B4: the person comes from settings ------------------------------------------------------------------------
def test_render_fills_the_name_the_about_line_and_the_subjects(ctx):
    from misty_engine import persona
    ctx.settings.set("user_name", "Sam")
    ctx.settings.set("user_line", "Sam is 38, an ICU nurse in Leeds.")
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status) VALUES ('a','Anatomy','t',1.0,'noted')")
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status) VALUES ('b','Pharmacology','t',2.0,'noted')")
    out = persona.render(ctx, "<<name>>'s tutor. <<about>> Ask <<name>> ({braces}) about <<subjects>>.")
    assert out == "Sam's tutor. Sam is 38, an ICU nurse in Leeds. Ask Sam ({braces}) about Anatomy or Pharmacology."


def test_with_no_name_the_text_still_reads_as_english_and_no_isaac_is_left(ctx):
    from misty_engine import chat, persona
    out = persona.render(ctx, "You are Misty, <<name>>'s assistant. <<about>> You chat with <<name>>.")
    assert out == "You are Misty, the user's assistant. You chat with the user."
    ctx.settings.set("user_name", "Sam")
    ctx.settings.set("user_line", "Sam is a made-up tester.")
    ctx.paths.wiki.mkdir(parents=True, exist_ok=True)
    (ctx.paths.wiki / "about-isaac.md").write_text("Sam is a made-up tester who likes bees.")    # the bootstrap card is known debt
    fixed, changing = chat.system_parts(ctx, "hello")
    vfixed, _ = chat.system_parts(ctx, "hello", voice=True)
    text = fixed + changing + vfixed + str(chat.tools_for(ctx))
    assert "Isaac" not in text and "Sam" in text and "<<" not in text
    ctx.settings.set("user_name", "")
    ctx.settings.set("user_line", "")
    text = "".join(chat.system_parts(ctx, "hello")) + str(chat.tools_for(ctx))
    assert "Isaac" not in text and "<<" not in text and "the user" in text


def test_the_class_notes_tool_lists_the_subjects_from_his_classes_not_the_source(ctx):
    from misty_engine import chat
    def subject_desc():
        t = next(t for t in chat.tools_for(ctx) if t["function"]["name"] == "class_notes")
        return t["function"]["parameters"]["properties"]["subject"]["description"]
    assert "Chem" not in subject_desc()
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status) VALUES ('a','Latin','t',1.0,'noted')")
    assert subject_desc() == "Latin"


def test_a_name_with_braces_or_tokens_inside_user_text_is_not_expanded_twice(ctx):
    from misty_engine import persona
    ctx.settings.set("user_name", "Sam")
    assert persona.render(ctx, "<<name>> said {x} and {{y}}") == "Sam said {x} and {{y}}"
