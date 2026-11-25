"""The Season 2 exam prompt is frozen (SPEC law 10): `chat.system_parts(exam=True)` must render byte for byte what it
rendered when step B started. The golden file was made from the code before any step B change.

If this fails you changed something the memory exam reads. Put new prompt blocks inside `if not exam`.
To rewrite the golden on purpose (only with Isaac's yes): MISTY_WRITE_GOLDEN=1 uv run pytest ../tests/engine/test_exam_frozen.py
"""
import os
from pathlib import Path

from misty_engine import chat, facts

GOLDEN = Path(__file__).parent / "fixtures" / "exam_prompt.golden.txt"
ABOUT = "Sam is a made-up person used only for tests. Sam studies, plays chess and keeps bees."


def render(ctx) -> str:
    ctx.settings.set("user_name", "Isaac")                       # his settings: the prompt now fills his name in from here
    ctx.settings.set("user_line", "Isaac is a student.")
    ctx.settings.set("user_curriculum", "IB")
    (ctx.paths.wiki).mkdir(parents=True, exist_ok=True)
    (ctx.paths.wiki / "about-isaac.md").write_text(ABOUT)
    for key, text in (("bee hives", "Sam keeps two hives behind the house."),
                      ("chess club", "Sam plays chess on Thursdays."),
                      ("bike", "Sam's bike is blue.")):
        facts.remember(ctx, key, text)
    fixed, changing = chat.system_parts(ctx, "what do I keep behind the house?", voice=False, exam=True)
    return f"=== FIXED ===\n{fixed}\n=== CHANGING ===\n{changing}\n"


def test_exam_prompt_is_byte_for_byte_the_same(ctx):
    got = render(ctx)
    if os.environ.get("MISTY_WRITE_GOLDEN"):
        GOLDEN.write_text(got)
    assert got == GOLDEN.read_text()


def test_the_exam_prompt_has_no_step_b_blocks(ctx):
    got = render(ctx)
    assert "How he wants you to be" not in got
