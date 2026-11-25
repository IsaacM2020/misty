import json

import pytest

from misty_engine.api import build_methods
from test_context_pack import put


@pytest.fixture
def wiki(fctx):
    put(fctx, "areas/school", "area", "School", "School.", importance=5)
    put(fctx, "projects/igem", "project", "iGEM", "iGEM wiki project.", importance=4, links=["people/aarav"],
        timeline=["2026-10-13: a", "2026-10-12: b", "2026-10-01: old"])
    put(fctx, "people/aarav", "person", "Aarav", "Teammate.", importance=3, links=["projects/igem"])
    p = fctx.wiki.read("projects/igem.md")
    fm = dict(p.fm)
    fm["area"] = "school"
    fctx.wiki.write("projects/igem.md", fm, p.body)
    return fctx


async def test_page_get_resolves_sources_and_dates(wiki, seed):
    seed("it_pg0000000000001", "text", title="source doc.md")
    p = wiki.wiki.read("projects/igem.md")
    fm = dict(p.fm)
    fm["sources"] = ["it_pg0000000000001"]
    wiki.wiki.write("projects/igem.md", fm, p.body)
    m = build_methods(wiki)
    out = await m["page.get"]({"path": "projects/igem.md"})
    assert out["sources"][0]["title"] == "source doc.md" and out["fm"]["type"] == "project"
    json.dumps(out)
    with pytest.raises(Exception):
        await m["page.get"]({"path": "../etc/passwd"})


async def test_correction_appends_scrubs_and_commits(wiki):
    m = build_methods(wiki)
    await m["correction.file"]({"path": "projects/igem.md", "text": "Aarav left the team. password: hunter2"})
    text = (wiki.paths.wiki / "corrections.md").read_text()
    assert "[[projects/igem]]: Aarav left the team." in text and "hunter2" not in text
    import subprocess
    log = subprocess.run(["git", "-C", str(wiki.paths.wiki), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "correction: filed" in log


async def test_context_and_query_methods_exist(wiki, fake_or):
    m = build_methods(wiki)
    assert {"query", "context", "page.get", "correction.file", "graph.get", "ask"} <= set(m)
    out = await m["context"]({"topic": "iGEM", "budget_tokens": 500})
    assert out["tokens"] <= 500 and "projects/igem.md" in out["pages"]
    assert (await m["graph.get"]({}))["nodes"][0]["id"] == "me"


async def test_page_get_follows_a_redirect_from_a_merged_page(wiki, seed):
    from misty_engine.registry.store import Registry
    m = build_methods(wiki)
    reg = Registry(wiki.con, wiki.clock)
    keep = reg.new_entity("project", "iGEM", status="active", path="projects/igem.md")
    old = reg.new_entity("project", "iGEM Project", status="active", path="projects/igem-project.md")
    reg.merge(keep, old, reason="R4")
    out = await m["page.get"]({"path": "projects/igem-project.md"})
    assert out["path"] == "projects/igem.md"
