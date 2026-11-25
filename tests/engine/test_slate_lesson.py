"""S10: a lesson (the old whiteboard scene) lands on the Slate: first send shows, later sends append only the new steps."""
import pytest

from misty_engine import draw, slate, slate_lesson, tutor

SCENE = {"title": "Energy at the top", "steps": [
    {"say": "Here is the ball, five metres up.", "do": [{"t": "axes", "x": [0, 10], "y": [0, 10], "bare": True}, {"t": "dot", "x": 5, "y": 5, "label": "ball"}]},
    {"say": "It has height, so which energy is that?", "do": [{"t": "seg", "x1": 5, "y1": 0, "x2": 5, "y2": 5, "arrow": True, "label": "h"}]},
    {"say": "So the formula is mgh.", "do": [{"t": "eq", "lines": ["E = mgh", "E = 2 \\times 9.8 \\times 5"]}]},
]}
ASKS = [False, True, False]


@pytest.fixture
def sent(ctx):
    out = []

    async def notify(m, p):
        out.append((m, p))
    ctx.notify = notify
    return out


@pytest.mark.asyncio
async def test_a_three_step_lesson_shows_then_appends_and_the_question_is_a_pause(ctx, sent):
    scene = draw.build(SCENE)
    await slate_lesson.show(ctx, tutor.partial(scene, 1), ASKS)             # stops at his question
    assert [m for m, _ in sent] == ["slate.show"]
    page = sent[0][1]
    assert page["kind"] == "stay" and [b["k"] for b in page["blocks"]] == ["diagram", "diagram", "turn"]
    assert all("mark" not in b for b in page["blocks"])                     # not an answer yet
    n0 = len(page["blocks"])
    await slate_lesson.show(ctx, scene, ASKS)                              # he got it: the rest arrives
    assert [m for m, _ in sent] == ["slate.show", "slate.append"]
    add = sent[1][1]
    assert add["from"] == n0 and [b["k"] for b in add["blocks"]] == ["math", "math"]
    assert add["blocks"][-1].get("mark") == "under" and add["blocks"][0]["say"] == "So the formula is mgh."
    assert slate.get(ctx, ctx.slate_id)["blocks"][-1] == add["blocks"][-1]  # history holds the whole lesson


@pytest.mark.asyncio
async def test_a_different_lesson_replaces_the_page(ctx, sent):
    await slate_lesson.show(ctx, draw.build(SCENE), ASKS)
    await slate_lesson.show(ctx, draw.build({**SCENE, "title": "Another"}), ASKS)
    assert [m for m, _ in sent] == ["slate.show", "slate.show"]
