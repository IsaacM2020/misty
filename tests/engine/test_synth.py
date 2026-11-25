import json

import pytest

from fakes import ScriptedRunner
from misty_engine.errors import CapHit
from misty_engine.pipeline import Worker
from misty_engine.registry import render
from misty_engine.types import approx_tokens
from misty_engine.wiki import priorities as pr
from misty_engine.wiki import synth
from misty_engine.wiki.notes import section
from test_render_lint import world  # noqa: F401


@pytest.fixture
def core(world):
    """A registry wiki with two real pages, one accepted area and a runner, ready for the core jobs."""
    ctx = world.ctx
    i1, _ = world.item("it_co0000000000a1", dated=[("2026-10-10", "kickoff")])
    i2, _ = world.item("it_co0000000000b2", dated=[("2026-10-16", "sync")])
    a = world.entity("project", "iGEM wiki", [i1, i2], summary="The team wiki for the iGEM competition.",
                     path="projects/igem-wiki.md", now=["Sync on 16 Oct"])
    b = world.entity("person", "Aarav Shah", [i1, i2], summary="Teammate on the wiki.", path="people/aarav-shah.md")
    for e in (a, b):
        render.write_entity(ctx, e)
    fm = {"type": "area", "title": "Coursework", "aliases": [], "area": None, "importance": 4, "status": "accepted",
          "updated": "2026-10-14", "sources": [], "notes": 0}
    ctx.wiki.write("areas/coursework.md", fm, "## Summary\nOne line.\n\n## Now\n\n## Timeline\n\n## Links\n")
    ctx.runner = ScriptedRunner()
    return ctx


async def run_step(ctx, kind):
    ctx.queue.enqueue(kind, payload={})
    await Worker(ctx, {kind: 1}).run_until_idle([kind])


async def test_areas_step_needs_accepted_areas_and_never_invents(world):
    ctx = world.ctx
    ctx.runner = ScriptedRunner()
    ctx.runner.set("areas", {"areas": [{"slug": "invented", "summary": "s"}]})
    jid = ctx.queue.enqueue("synth_areas", payload={})
    await Worker(ctx, {"synth_areas": 1}).run_until_idle(["synth_areas"])
    j = ctx.queue.get(jid)
    assert j.status == "failed" and j.error_code == "AREAS_NOT_ACCEPTED"
    assert len(ctx.runner.calls) == 0 and not ctx.wiki.exists("areas/invented.md")


async def test_areas_step_only_rewrites_descriptions_of_accepted_areas(core):
    core.runner.set("areas", {"areas": [{"slug": "coursework", "summary": "Work for school. Lots of deadlines."},
                                        {"slug": "invented", "summary": "ghost"}]})
    await run_step(core, "synth_areas")
    page = core.wiki.read("areas/coursework.md")
    assert section(page.body, "Summary") == "Work for school. Lots of deadlines."
    assert page.fm["status"] == "accepted" and page.fm["title"] == "Coursework"
    assert not core.wiki.exists("areas/invented.md") and len(core.runner.calls) == 1


async def test_about_card_is_capped_at_500_tokens_and_remembers_when(core):
    core.runner.set("about", {"about": "A fact about the person. " * 400})
    r = await synth.about_card(core)
    body = core.wiki.read("about-isaac.md").body
    assert approx_tokens(body) <= 500 and body.endswith(".") and r["about_tokens"] <= 500
    assert core.settings.get("about_card_at") is not None and core.settings.get("about_card_notes") == 2


async def test_about_card_is_skipped_when_pinned(core):
    core.settings.set("about_pinned", True)
    core.runner.set("about", {"about": "x."})
    assert await synth.about_card(core) == {"skipped": "PINNED"}
    assert core.runner.calls == []


async def test_about_card_waits_for_forty_new_notes(core):
    core.runner.set("about", {"about": "First card."})
    await synth.about_card(core)
    assert (await synth.about_card(core)) == {"skipped": "TOO_FEW_NEW_NOTES"}
    core.settings.set("about_card_notes", 2 - 40)                      # as if 40 notes arrived since
    core.runner.set("about", {"about": "Second card."})
    assert "about_tokens" in await synth.about_card(core)
    assert core.wiki.read("about-isaac.md").body.strip() == "Second card."


async def test_about_prompt_carries_corrections_and_the_previous_card(core):
    core.wiki.write_text("corrections.md", "# Corrections\n- 2026-10-15T03:00:00Z [[about-isaac]]: no fixed major\n")
    core.wiki.write_text("about-isaac.md", "---\ntype: about\ntitle: About\nupdated: x\n---\n\nThe old card.\n")
    core.runner.set("about", {"about": "New card."})
    await synth.about_card(core)
    p = core.runner.prompts[0]
    assert "no fixed major" in p and "The old card." in p and "orrections win" in p
    c = core.runner.calls[0]
    assert c["purpose"] == "about" and c["model_key"] == "haiku"


async def test_priorities_draft_points_only_at_real_areas_and_only_once(core):
    core.runner.set("priorities", {"priorities": [
        {"text": "Ship the iGEM wiki", "area": "coursework", "why": "deadline"},
        {"text": "Something else", "area": "made-up-area", "why": "x"}]})
    assert await synth.draft_priorities(core) == 2
    got = pr.get(core.wiki)
    assert got["status"] == "draft" and got["lines"][0]["area"] == "coursework" and got["lines"][1]["area"] is None
    assert await synth.draft_priorities(core) == 0 and len(core.runner.calls) == 1     # never overwrites


async def test_sub_cap_hit_defers_the_step_and_it_resumes(core):
    """A purpose over its sub-cap makes the step wait (never fail); the next budget window releases it."""
    core.runner.set("about", {"about": "Card."})
    core.runner.raise_once(CapHit("CAP_PURPOSE:core"))
    jid = core.queue.enqueue("synth_about", payload={})
    await Worker(core, {"synth_about": 1}).run_until_idle(["synth_about"])
    assert core.queue.get(jid).status == "waiting_cap"
    assert core.queue.release("waiting_cap") == 1
    await Worker(core, {"synth_about": 1}).run_until_idle(["synth_about"])
    assert core.queue.get(jid).status == "done"


async def test_synth_calls_carry_their_purposes(core):
    core.runner.set("areas", {"areas": [{"slug": "coursework", "summary": "s"}]})
    core.runner.set("about", {"about": "x."})
    core.runner.set("priorities", {"priorities": []})
    await synth.run_all(core)
    assert {c["schema"]: c["purpose"] for c in core.runner.calls} == {
        "areas": "areas", "about": "about", "priorities": "priorities"}


async def test_chain_runs_through_to_index(core):
    core.runner.set("areas", {"areas": [{"slug": "coursework", "summary": "s"}]})
    core.runner.set("about", {"about": "x."})
    core.runner.set("priorities", {"priorities": []})
    core.queue.enqueue("synth_areas", payload={})
    kinds = ["synth_areas", "synth_about", "synth_priorities", "synth_index"]
    await Worker(core, {k: 1 for k in kinds}).run_until_idle(kinds)
    assert core.queue.counts().get("failed", 0) == 0 and core.runner.schemas == ["areas", "about", "priorities"]
    assert "[[projects/igem-wiki]]" in (core.paths.wiki / "index.md").read_text()
    assert core.settings.get("phase") == "live" and core.settings.get("synth_done") is True


async def test_synth_prompts_contain_only_wiki_text(core):
    core.con.execute("INSERT INTO items(id, source, kind, locator, title, status, updated_at) "
                     "VALUES ('it_raw000000000001','folders','file','/x','RAW-ONLY-LINE title','scrubbed',0)")
    core.con.execute("INSERT INTO item_chunks(item_id, chunk_no, text_scrubbed) VALUES "
                     "('it_raw000000000001', 0, 'RAW-ONLY-LINE that must never appear in any core prompt')")
    core.runner.set("areas", {"areas": [{"slug": "coursework", "summary": "s"}]})
    core.runner.set("about", {"about": "x."})
    core.runner.set("priorities", {"priorities": []})
    await synth.run_all(core)
    assert core.runner.prompts and not any("RAW-ONLY-LINE" in p for p in core.runner.prompts)


def test_priorities_apply_sets_importance_in_the_registry(core):
    from misty_engine.api import build_methods
    import asyncio
    pr.write_draft(core.wiki, [{"text": "Ship the iGEM wiki", "area": None}], core.clock)
    asyncio.run(build_methods(core)["priorities.apply"]({"accepted_line_ids": []}))
    row = core.con.execute("SELECT importance FROM entities WHERE name='iGEM wiki'").fetchone()
    assert row["importance"] == 5                                     # named in a priority, and it sticks
    render.write_entity(core, core.con.execute("SELECT id FROM entities WHERE name='iGEM wiki'").fetchone()[0])
    assert core.wiki.read("projects/igem-wiki.md").fm["importance"] == 5
