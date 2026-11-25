"""Live mode's hearing: Jev decides who a phrase was for; only to-dos from other people's talk are kept."""
from __future__ import annotations

import pytest

from misty_engine import facts, hearing
from misty_engine.errors import Retryable
from misty_engine.jev_wire import Answer, to_request


class FakeJev:
    def __init__(self, to=("misty", 0.9), finished=("yes", 0.9), todo=("no", 0.9), fail=False):
        self.a = {"to": Answer((to[0],), to[1]), "finished": Answer((finished[0],), finished[1]),
                  "todo": Answer((todo[0],), todo[1])}
        self.fail, self.calls = fail, []

    async def ask(self, context, questions, **kw):
        self.calls.append({"context": context, **kw})
        if self.fail:
            raise Retryable("TIMEOUT")
        return self.a


@pytest.mark.asyncio
async def test_a_question_for_misty_is_for_misty(ctx):
    ctx.jev = FakeJev()
    out = await hearing.hear(ctx, "what's 23 times 47", last_reply="Hi Isaac.", since_reply=4)
    assert out == {"to": "misty", "finished": True, "todo_saved": False}
    c = ctx.jev.calls[0]
    assert "Misty last said (4 s ago): Hi Isaac." in c["context"] and c["purpose"] == "hear"
    assert c["hints"]["someone_else"].startswith("The speaker is talking with another person")


@pytest.mark.asyncio
async def test_unsure_stays_quiet(ctx):
    ctx.jev = FakeJev(to=("misty", 0.55))
    assert (await hearing.hear(ctx, "can you pass that"))["to"] == "unsure"
    ctx.jev = FakeJev(fail=True)
    assert (await hearing.hear(ctx, "can you pass that"))["to"] == "unsure"


@pytest.mark.asyncio
async def test_only_a_to_do_from_other_talk_is_kept(ctx):
    ctx.jev = FakeJev(to=("someone_else", 0.9), todo=("yes", 0.8))
    out = await hearing.hear(ctx, "Isaac dinner is at 8 tonight")
    assert out["todo_saved"] and out["to"] == "someone_else"
    kept = [r["text"] for r in facts.active(ctx.con)]
    assert len(kept) == 1 and "to-do for Isaac): Isaac dinner is at 8 tonight" in kept[0]
    ctx.jev = FakeJev(to=("someone_else", 0.9), todo=("no", 0.9))
    await hearing.hear(ctx, "did you see the match last night")
    assert len(facts.active(ctx.con)) == 1                        # small talk is never stored


@pytest.mark.asyncio
async def test_past_the_daily_cap_nothing_is_sent(ctx, monkeypatch):
    ctx.jev = FakeJev()
    monkeypatch.setattr(ctx.spend, "spent_purpose", lambda *a, **k: hearing.DAILY_CAP)
    assert (await hearing.hear(ctx, "what's the time"))["to"] == "unsure" and ctx.jev.calls == []


def test_choice_hints_reach_the_request():
    body = to_request("typesafe/jev-1.13", "Heard just now: hi", [hearing.TO], hearing.HINTS)
    assert body["questions"]["to"]["criteria"]["nobody"].startswith("It is background")
