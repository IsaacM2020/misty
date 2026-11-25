import json

import pytest

from misty_engine import quality
from misty_engine.api import build_methods
from misty_engine.errors import Fatal
from misty_engine.registry import render
from misty_engine.wiki import summaries
from test_render_lint import world  # noqa: F401


def two_pages(world):
    ctx = world.ctx
    i1, _ = world.item("it_ea0000000000a1", dated=[("2026-10-01", "met Aarav")])
    i2, _ = world.item("it_ea0000000000b2", dated=[("2026-10-05", "met Aarav S")])
    i3, _ = world.item("it_ea0000000000c3", dated=[("2026-10-08", "call")])
    a = world.entity("person", "Aarav Shah", [i1, i2], summary="Classmate on the team.")
    b = world.entity("person", "Aarav S", [i2, i3], summary="A teammate called Aarav.")
    for e in (a, b):
        assert render.write_entity(ctx, e)
    render.refresh_links(ctx, [a, b])                      # each page now knows the other exists
    return a, b


@pytest.mark.asyncio
async def test_merge_via_rpc_leaves_one_page_and_a_redirect(world):
    ctx = world.ctx
    a, b = two_pages(world)
    m = build_methods(ctx)
    old_b = ctx.con.execute("SELECT path FROM entities WHERE id=?", (b,)).fetchone()[0]
    out = await m["entity.merge"]({"keep": a, "drop": b})
    assert out["merge_id"] > 0 and out["path"] == "people/aarav-shah.md"
    assert not ctx.wiki.exists(old_b) and ctx.wiki.exists(out["path"])
    page = await m["page.get"]({"path": old_b})                        # the old path still resolves
    assert page["path"] == out["path"]
    assert summaries.stale_entities(ctx) == [a]                         # re-summarised with both sources
    aliases = json.dumps(ctx.wiki.read(out["path"]).fm["aliases"])
    assert "Aarav S" in aliases


@pytest.mark.asyncio
async def test_unmerge_brings_both_pages_back_identical(world):
    ctx = world.ctx
    a, b = two_pages(world)
    before = {p: ctx.wiki.read(p).body for p in ("people/aarav-shah.md", "people/aarav-s.md")}
    counts = quality.report(ctx.paths.wiki, ctx.con)
    m = build_methods(ctx)
    out = await m["entity.merge"]({"keep": a, "drop": b})
    back = await m["entity.unmerge"]({"merge_id": out["merge_id"]})
    assert back == {"keep": a, "restored": b}
    after = {p: ctx.wiki.read(p).body for p in before}
    assert after == before
    assert quality.report(ctx.paths.wiki, ctx.con) == counts
    with pytest.raises(Fatal):
        await m["entity.unmerge"]({"merge_id": out["merge_id"]})        # already undone


@pytest.mark.asyncio
async def test_rename_pins_the_name_and_keeps_the_path(world):
    ctx = world.ctx
    a, _ = two_pages(world)
    out = await build_methods(ctx)["entity.rename"]({"id": a, "name": "Aarav Shah Jr"})
    assert out["path"] == "people/aarav-shah.md"
    page = ctx.wiki.read(out["path"])
    assert page.fm["title"] == "Aarav Shah Jr"
    row = ctx.con.execute("SELECT name, name_pinned FROM entities WHERE id=?", (a,)).fetchone()
    assert (row["name"], row["name_pinned"]) == ("Aarav Shah Jr", 1)


@pytest.mark.asyncio
async def test_list_merges_has_ids_and_names_only(world):
    ctx = world.ctx
    a, b = two_pages(world)
    m = build_methods(ctx)
    await m["entity.merge"]({"keep": a, "drop": b})
    rows = (await m["entity.list_merges"]({"limit": 5}))["merges"]
    assert rows[0]["kept"] == a and rows[0]["dropped"] == b and rows[0]["reason"] == "user"
    assert rows[0]["kept_name"] == "Aarav Shah" and "snapshot" not in rows[0]


@pytest.mark.asyncio
async def test_bad_ids_and_kind_mismatch_are_refused(world):
    ctx = world.ctx
    a, _ = two_pages(world)
    t = world.entity("topic", "Python", [world.item("it_eb0000000000a1")[0]])
    m = build_methods(ctx)
    for bad in ({"keep": a, "drop": "en_nope"}, {"keep": a, "drop": a}, {"keep": a, "drop": t}, {}):
        with pytest.raises(Fatal):
            await m["entity.merge"](bad)
    with pytest.raises(Fatal):
        await m["entity.rename"]({"id": a, "name": "  "})
