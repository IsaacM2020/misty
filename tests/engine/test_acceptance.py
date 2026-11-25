import pytest

from misty_engine import acceptance
from misty_engine.registry import render
from test_render_lint import world  # noqa: F401


def test_spend_by_purpose_sums_and_divides_by_compiled_items(world):
    ctx = world.ctx
    for iid in ("it_ac0000000000a1", "it_ac0000000000b2"):
        world.item(iid)
    ctx.con.execute("UPDATE items SET updated_at=100")
    for purpose, usd in (("compile", 0.002), ("compile", 0.001), ("judge", 0.0004), (None, 0.0001)):
        ctx.con.execute("INSERT INTO spend(ts, provider, model, usd, purpose) VALUES (200,'openrouter','m',?,?)",
                        (usd, purpose))
    out = acceptance.spend_by_purpose(ctx, since_ts=50)
    assert out["compiled_items"] == 2 and out["total_usd"] == pytest.approx(0.0035)
    assert out["by_purpose"]["compile"] == {"usd": 0.003, "calls": 2}
    assert out["per_1000_items"]["compile"] == pytest.approx(1.5)
    assert "(none)" in out["by_purpose"]
    assert acceptance.spend_by_purpose(ctx, since_ts=500)["total_usd"] == 0


@pytest.mark.asyncio
async def test_acceptance_rows_are_all_ok_on_a_clean_world(world):
    ctx = world.ctx
    i1, _ = world.item("it_ac0000000000c3", dated=[("2026-10-10", "kickoff")])
    i2, _ = world.item("it_ac0000000000d4", dated=[("2026-10-11", "sync")])
    e = world.entity("project", "iGEM wiki", [i1, i2], summary="The team wiki.", path="projects/igem-wiki.md")
    render.write_entity(ctx, e)
    rows = await acceptance.run(ctx)
    assert rows and all(r.ok for r in rows), acceptance.format_rows(rows)
    assert {r.id for r in rows} >= {"A2", "A4", "A5", "A7"}


@pytest.mark.asyncio
async def test_a_dead_link_and_a_private_leak_fail_their_rows(world):
    ctx = world.ctx
    ctx.wiki.write("topics/x.md", {"type": "topic", "title": "X", "updated": "2026-10-12", "sources": []},
                   "## Summary\nA thing.\n\n## Now\n\n## Timeline\n\n## Links\n- [[people/ghost]]\n")
    rows = await acceptance.run(ctx)
    bad = {(r.id, r.name) for r in rows if not r.ok}
    assert ("A2", "dead_links") in bad
    text = acceptance.format_rows(rows)
    assert "FAIL" in text and "dead_links" in text
