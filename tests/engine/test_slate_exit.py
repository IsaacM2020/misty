"""S8: how the Slate ends. A protocol is read back and waits for ok / change N to X / anything else, with no model for the first two."""
import pytest

from misty_engine import slate

from test_chat import ScriptBrain, call, say

PROTO = {"title": "Streaking", "blocks": [
    {"k": "steps", "items": ["Flame the loop", "Touch the plate", "Drag it across", "Incubate overnight"], "confirm": True,
     "say": ["Flame the loop.", "Touch the plate.", "Drag it across.", "Incubate overnight."]}]}


async def opened(ctx, raw=PROTO):
    sent = []

    async def notify(method, params):
        sent.append((method, params))
    ctx.notify = notify
    ctx.openrouter = ScriptBrain({"calls": [call("slate", slate=raw)]}, {"content": slate.ASK})
    await say(ctx, "walk me through streaking a plate")
    return sent


def names(sent):
    return [m for m, _ in sent]


def test_a_confirm_slate_stays_and_asks():
    page = slate.build(PROTO)
    assert page["kind"] == "stay" and page["ask"] == slate.ASK


def test_a_short_slate_is_quick_and_has_no_ask():
    page = slate.build({"blocks": [{"k": "math", "tex": "5 \\times 5 = \\ans", "ans": {"expr": "5*5"}, "say": "Twenty-five."}]})
    assert page["kind"] == "quick" and "ask" not in page


@pytest.mark.asyncio
async def test_the_model_is_told_to_say_the_read_back(ctx):
    ctx.openrouter = ScriptBrain({"calls": [call("slate", slate=PROTO)]}, {"content": slate.ASK})

    async def notify(m, p):
        pass
    ctx.notify = notify
    await say(ctx, "streak a plate")
    tool_msg = [m for m in ctx.openrouter.bodies[-1]["messages"] if m.get("role") == "tool"][-1]["content"]
    assert slate.ASK in tool_msg and ctx.slate_pending is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["ok", "Yes", "looks good", "that's fine.", "Good"])
async def test_ok_closes_and_saves_without_the_model(ctx, word):
    sent = await opened(ctx)
    brain, left = ctx.openrouter, len(ctx.openrouter.bodies)
    out, text, _ = await say(ctx, word)
    assert ("slate.close", {"saved": True}) in sent and ctx.slate_saved is not None and ctx.slate_pending is None
    assert out["reply"] == "Done." and len(brain.bodies) == left


@pytest.mark.asyncio
@pytest.mark.parametrize("word,n", [("change 2 to touch the agar", 2), ("Change step three to drag it in a zigzag", 3), ("change the first one to flame the loop twice", 1)])
async def test_change_updates_only_that_line_and_asks_again(ctx, word, n):
    sent = await opened(ctx)
    out, _, _ = await say(ctx, word)
    upd = [p for m, p in sent if m == "slate.update"]
    assert len(upd) == 1 and upd[0]["item"] == n - 1 and upd[0]["block"] == 0
    assert ctx.slate_page["blocks"][0]["items"][n - 1] == upd[0]["text"]
    assert [x for i, x in enumerate(ctx.slate_page["blocks"][0]["items"]) if i != n - 1] == [x for i, x in enumerate(PROTO["blocks"][0]["items"]) if i != n - 1]
    assert out["reply"].endswith(slate.ASK) and ctx.slate_pending is not None and "slate.close" not in names(sent)


@pytest.mark.asyncio
async def test_change_something_asks_which_step(ctx):
    sent = await opened(ctx)
    out, _, _ = await say(ctx, "I want to change something")
    assert "Which step" in out["reply"] and "slate.update" not in names(sent) and ctx.slate_pending is not None


@pytest.mark.asyncio
async def test_a_step_that_does_not_exist_changes_nothing(ctx):
    sent = await opened(ctx)
    out, _, _ = await say(ctx, "change 9 to nothing")
    assert "no step 9" in out["reply"] and "slate.update" not in names(sent)


@pytest.mark.asyncio
async def test_anything_else_is_a_normal_turn_and_the_slate_stays(ctx):
    sent = await opened(ctx)
    ctx.openrouter = ScriptBrain({"content": "It is half past four."})
    out, _, _ = await say(ctx, "what's the time")
    assert out["reply"] == "It is half past four." and ctx.slate_pending is not None
    assert "slate.close" not in names(sent) and ctx.slate_page is not None


@pytest.mark.asyncio
async def test_an_unanswered_read_back_is_forgotten_after_fifteen_minutes(ctx):
    sent = await opened(ctx)
    ctx.clock.advance(slate.PENDING_SECONDS + 5) if hasattr(ctx.clock, "advance") else setattr(ctx.clock, "t", ctx.clock.now() + slate.PENDING_SECONDS + 5)
    ctx.openrouter = ScriptBrain({"content": "Sure, ok."})
    out, _, _ = await say(ctx, "ok")
    assert out["reply"] == "Sure, ok." and ctx.slate_pending is None and "slate.close" not in names(sent)


@pytest.mark.asyncio
async def test_wait_holds_a_quick_slate_but_only_while_it_is_up(ctx):
    sent = []

    async def notify(m, p):
        sent.append(m)
    ctx.notify = notify
    quick = {"blocks": [{"k": "math", "tex": "5 \\times 5 = \\ans", "ans": {"expr": "5*5"}, "say": "Twenty-five."}]}
    ctx.openrouter = ScriptBrain({"calls": [call("slate", slate=quick)]}, {"content": "There."})
    await say(ctx, "what's 5x5")
    out, _, _ = await say(ctx, "wait")
    assert "slate.hold" in sent and out["reply"] == "Sure."
    sent.clear()
    ctx.clock.t = ctx.clock.now() + slate.HOLD_SECONDS + 5 if hasattr(ctx.clock, "t") else 0
    ctx.openrouter = ScriptBrain({"content": "Wait for what?"})
    out, _, _ = await say(ctx, "wait")
    assert "slate.hold" not in sent
