"""Card B4: with HIS settings, the normal (non-exam) prompt, the voice prompt and the tool list render byte for byte as they
did before the person was taken out of the source. The golden file was made from the code before B4 changed anything.

His settings here: user_name "Isaac", user_line "Isaac is a student.", and six subjects seen in class_sessions
(the `class_notes` tool's subject list now comes from there). To rewrite on purpose (only with Isaac's yes):
MISTY_WRITE_GOLDEN=1 uv run pytest ../tests/engine/test_prompt_golden.py
"""
import json
import os
from pathlib import Path

from misty_engine import cares, chat, facts, ways

GOLDEN = Path(__file__).parent / "fixtures" / "prompt_normal.golden.txt"
ABOUT = "Sam is a made-up person used only for tests. Sam studies, plays chess and keeps bees."
SUBJECTS = ["Chem", "Math", "Physics", "Econ", "English", "Spanish"]


def his_settings(ctx):
    ctx.settings.set("user_name", "Isaac")
    ctx.settings.set("user_line", "Isaac is a student.")
    ctx.settings.set("user_curriculum", "IB")
    for i, sub in enumerate(SUBJECTS):
        ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status) VALUES (?,?,?,?, 'noted')",
                        (f"cs_golden{i}", sub, f"{sub} class", 1_000_000.0 + i))


def render(ctx) -> str:
    his_settings(ctx)
    ctx.paths.wiki.mkdir(parents=True, exist_ok=True)
    (ctx.paths.wiki / "about-isaac.md").write_text(ABOUT)
    for key, text in (("bee hives", "Sam keeps two hives behind the house."), ("chess club", "Sam plays chess on Thursdays."),
                      ("bike", "Sam's bike is blue.")):
        facts.remember(ctx, key, text)
    cares.set_care(ctx, "chess", "Sam wants to get better at chess", "goal", "high")
    ways.set_way(ctx, "tone", "Dry and understated.", "tone")
    fixed, changing = chat.system_parts(ctx, "what do I keep behind the house?", voice=False, exam=False)
    vfixed, vchanging = chat.system_parts(ctx, "what do I keep behind the house?", voice=True, exam=False)
    tools = chat.tools_for(ctx) if hasattr(chat, "tools_for") else chat.TOOLS
    return (f"=== FIXED ===\n{fixed}\n=== CHANGING ===\n{changing}\n=== VOICE FIXED ===\n{vfixed}\n"
            f"=== VOICE CHANGING ===\n{vchanging}\n=== TOOLS ===\n{json.dumps(tools, indent=1, ensure_ascii=False)}\n")


def test_normal_prompt_voice_prompt_and_tools_are_byte_for_byte_the_same_with_his_settings(ctx):
    got = render(ctx)
    if os.environ.get("MISTY_WRITE_GOLDEN"):
        GOLDEN.write_text(got)
    assert got == GOLDEN.read_text()


def test_the_text_prompt_does_not_depend_on_the_voice_block(ctx, monkeypatch):
    """V2: changing how voice replies are written must leave the text path byte for byte the same."""
    from misty_engine import chat
    a = chat.system_parts(ctx, "what is 27/48?", voice=False, exam=False)
    monkeypatch.setattr(chat, "VOICE", "Voice: something completely different")
    b = chat.system_parts(ctx, "what is 27/48?", voice=False, exam=False)
    assert a == b
    assert "Voice:" not in a[0] + a[1]
    v = chat.system_parts(ctx, "what is 27/48?", voice=True, exam=False)
    assert "something completely different" in v[0] + v[1]                   # and the voice path does use it


def test_the_voice_block_asks_for_the_answer_first_and_no_lists():
    from misty_engine import chat
    v = chat.VOICE
    assert "answer comes first" in v and "Never a list" in v and "Do not read out your working" in v
