"""S9: every shown slate is saved with its turn id and reopens identical."""
import json
from pathlib import Path

import pytest

from misty_engine import slate

from test_chat import ScriptBrain, call, say

FIX = Path(__file__).parent / "fixtures" / "slates" / "good"


def pages(n=50):
    out = []
    for f in sorted(FIX.glob("*.json")):
        raw = json.loads(f.read_text())
        try:
            out.append(slate.build(raw.get("slate", raw)))
        except ValueError:
            pass
    return (out * 2)[:n]


def test_fifty_slates_are_saved_and_reopened_identical(ctx):
    ps = pages(50)
    assert len(ps) == 50
    ids = []
    for i, p in enumerate(ps):
        ctx.turn = f"t{i}"
        ids.append(slate.save(ctx, p))
    for sid, p in zip(ids, ps):
        assert slate.get(ctx, sid) == p
    lst = slate.listing(ctx)
    assert len(lst) == 50 and {r["turn"] for r in lst} == {f"t{i}" for i in range(50)}


@pytest.mark.asyncio
async def test_a_shown_slate_is_saved_with_its_turn_and_a_change_updates_it(ctx):
    async def notify(m, p):
        pass
    ctx.notify = notify
    raw = {"title": "Streaking", "blocks": [{"k": "steps", "items": ["Flame it", "Touch it"], "confirm": True, "say": ["a", "b"]}]}
    ctx.openrouter = ScriptBrain({"calls": [call("slate", slate=raw)]}, {"content": slate.ASK})
    ctx.turn = "tabc"
    await say(ctx, "streak a plate")
    (row,) = slate.listing(ctx)
    assert row["turn"] == "tabc" and row["title"] == "Streaking"
    await say(ctx, "change 2 to touch the agar")
    assert slate.get(ctx, row["id"])["blocks"][0]["items"][1] == "touch the agar"
