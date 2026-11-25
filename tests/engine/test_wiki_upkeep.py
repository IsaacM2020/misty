import pytest

from fakes import ScriptedRunner
from misty_engine.api import build_methods
from misty_engine.wiki import health, priorities as pr
from test_context_pack import put


@pytest.fixture
def claude(fctx):
    c = ScriptedRunner()
    fctx.runner = c
    return c


@pytest.fixture
def wiki(fctx):
    put(fctx, "projects/igem", "project", "iGEM", "(stub)", importance=4, timeline=["2026-10-16: wiki sync"])
    put(fctx, "people/aarav", "person", "Aarav", "Teammate.", timeline=["2026-10-12: sent deadline"])
    return fctx


def test_health_finds_broken_orphan_stale_and_big_card(fctx):
    put(fctx, "projects/a", "project", "A", "s", links=["projects/ghost"])
    put(fctx, "topics/lonely", "topic", "Lonely", "s")
    fctx.con.execute("INSERT INTO sources_state(source, health, last_ok_at) VALUES ('gmail','ok',?)",
                     (fctx.clock.now() - 3 * 86400,))
    fctx.settings.set("sources_enabled", {"gmail": True})
    fctx.wiki.write_text("about-isaac.md", "---\ntype: about\ntitle: About\nupdated: x\n---\n\n" + "word " * 700)
    fctx.con.execute("INSERT INTO items(id, source, kind, locator, status, updated_at) "
                     "VALUES ('it_hf00000000000001','folders','file','/x','compile_failed',0)")
    rep = health.check(fctx)
    assert "projects/a -> projects/ghost" in rep.broken_links and "topics/lonely" in rep.orphans
    assert rep.stale_sources == ["gmail"] and rep.about_tokens > 500 and rep.compile_failed == ["it_hf00000000000001"]
    assert rep.ok is False
    health.write_report(fctx, rep)
    assert fctx.wiki.exists("health.md") and fctx.settings.get("health_summary")["ok"] is False


async def test_priorities_weekly_proposal_diff_and_apply(wiki, claude):
    pr.write_draft(wiki.wiki, [{"text": "Ship iGEM wiki"}, {"text": "NOI prep"}], wiki.clock)
    pr.apply(wiki.wiki, wiki.clock)
    claude.set("priorities_weekly", {"priorities": [{"text": "Ship iGEM wiki"}, {"text": "IB reading"}]})
    n = await pr.propose(wiki)
    assert n == 2 and wiki.wiki.exists("priorities.proposed.md")
    m = build_methods(wiki)
    d = (await m["priorities.diff"]({}))["diff"]
    assert {(x["op"], x["text"]) for x in d} == {("remove", "NOI prep"), ("add", "IB reading")}
    add_id = next(x["id"] for x in d if x["op"] == "add")
    await m["priorities.apply"]({"accepted_line_ids": [add_id]})
    texts = [l["text"] for l in (await m["priorities.get"]({}))["lines"]]
    assert texts == ["Ship iGEM wiki", "IB reading", "NOI prep"] and not wiki.wiki.exists("priorities.proposed.md")
