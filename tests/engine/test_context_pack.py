import pytest

from misty_engine.wiki.context import build_context, match_pages


def put(ctx, path, kind, title, summary, importance=3, links=(), timeline=(), now=()):
    fm = {"type": kind, "title": title, "aliases": [], "area": None, "importance": importance,
          "updated": "2026-10-14", "sources": []}
    body = (f"## Summary\n{summary}\n\n## Now\n" + "\n".join(f"- {n}" for n in now) + "\n\n## Timeline\n" +
            "\n".join(f"- {t}" for t in timeline) + "\n\n## Links\n" + "\n".join(f"- [[{l}]]" for l in links) + "\n")
    ctx.wiki.write(path + ".md", fm, body)
    text = f"{title} {summary}"
    ctx.index.upsert_page(path + ".md", text, ctx.embedder.embed_documents([text])[0])


@pytest.fixture
def ctx_wiki(fctx):
    put(fctx, "projects/igem", "project", "iGEM wiki", "The iGEM wiki documents the team project.",
        importance=4, links=["people/aarav-shah"], now=["write part 3 docs"],
        timeline=["2026-10-16: wiki sync", "2026-10-10: kickoff"])
    put(fctx, "people/aarav-shah", "person", "Aarav Shah", "Aarav is on the iGEM team.", importance=4,
        timeline=["2026-10-12: sent the deadline"])
    return fctx


@pytest.mark.parametrize("budget", [100, 500, 2000, 8000])
def test_pack_never_exceeds_budget(ctx_wiki, budget):
    assert build_context(ctx_wiki, "iGEM wiki", budget).tokens <= budget


def test_about_card_always_first(ctx_wiki):
    assert build_context(ctx_wiki, "anything", 2000).text.startswith("## about-isaac")


def test_summary_and_now_before_timeline(ctx_wiki):
    t = build_context(ctx_wiki, "iGEM wiki", 800).text
    assert "Summary:" in t and "Now:" in t
    if "Timeline:" in t:
        assert t.index("Summary:") < t.index("Timeline:")


def test_match_pages_excludes_source_notes(ctx_wiki):
    text = "iGEM wiki raw source note"
    ctx_wiki.index.upsert_page("sources/gmail/x.md", text, ctx_wiki.embedder.embed_documents([text])[0])
    assert all(not p.startswith("sources/") for p, _ in match_pages(ctx_wiki, "iGEM wiki"))


def test_one_hop_only_importance_3_plus(fctx):
    put(fctx, "projects/a", "project", "Alpha topic", "alpha alpha", importance=4, links=["projects/b"])
    put(fctx, "projects/b", "project", "Bravo", "bravo", importance=2, links=["projects/c"])
    put(fctx, "projects/c", "project", "Charlie", "charlie", importance=5)
    pages = build_context(fctx, "Alpha topic alpha", 8000).pages
    assert "projects/a.md" in pages and "projects/b.md" not in pages and "projects/c.md" not in pages
    put(fctx, "projects/a", "project", "Alpha topic", "alpha alpha", importance=4, links=["projects/d"])
    put(fctx, "projects/d", "project", "Delta", "delta", importance=3)
    assert "projects/d.md" in build_context(fctx, "Alpha topic alpha", 8000).pages


def test_search_chunks_only_when_under_half_budget(ctx_wiki, seed):
    seed("it_rawchunk00000001", "iGEM wiki raw notes about the part documentation", title="raw.md")
    small = build_context(ctx_wiki, "iGEM wiki", 8000)
    assert "search_chunks" in small.sources_used
    big = build_context(ctx_wiki, "iGEM wiki", 120)
    assert "search_chunks" not in big.sources_used


def test_empty_wiki_returns_card_only(fctx):
    pack = build_context(fctx, "anything", 500)
    assert pack.text.startswith("## about-isaac") and pack.pages == []


def test_budget_zero_or_tiny_is_safe(ctx_wiki):
    assert build_context(ctx_wiki, "x", 1).tokens <= 1
    assert build_context(ctx_wiki, "x", 0).text == ""


def test_match_pages_ranks_stale_pages_below_current_ones(fctx):
    # same words, but one page has not moved in two years (fake clock is 2026-10-15 SGT)
    put(fctx, "topics/z-new-physics-papers", "topic", "Physics past papers", "Physics past papers to do.",
        timeline=["2026-10-07: practice paper posted"])
    put(fctx, "topics/a-old-physics-papers", "topic", "Physics past papers", "Physics past papers to do.",
        timeline=["2024-09-15: papers added"])
    got = [p for p, _ in match_pages(fctx, "physics past papers")]
    assert got.index("topics/z-new-physics-papers.md") < got.index("topics/a-old-physics-papers.md")
