import time
from datetime import datetime, timezone

import pytest

from misty_engine.search import SOURCE_FILTERS, rrf_merge, search, when_bounds


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


DAY = 86400


@pytest.fixture
def indexed_ctx(ctx, add_item, emb, clock):
    now = clock.now()
    rows = [
        ("it_000000000000000f", "folders", "file", "igem_wiki_draft.md", now - 1 * DAY,
         ["iGEM wiki draft, deadline is Friday, synbio team"], "scrubbed"),
        ("it_0000000000000010", "folders", "file", "physics_ia.md", now - 400 * DAY,
         ["physics internal assessment draft on a pendulum"], "scrubbed"),
        ("it_0000000000000011", "repos", "repo", "cricket-model", now - 3 * DAY,
         ["cricket win predictor repository with README"], "scrubbed"),
        ("it_0000000000000012", "folders", "image", "igem_poster.png", now - 2 * DAY,
         ["igem_poster.png"], "done"),
        ("it_0000000000000013", "gmail", "mail_thread", "iGEM access", now - 1 * DAY,
         ["iGEM wiki access request from the team"], "scrubbed"),
        ("it_0000000000000014", "folders", "file", "igem_copy.md", now - 1 * DAY,
         ["iGEM wiki draft, deadline is Friday, synbio team"], "skipped"),
    ]
    for iid, src, kind, title, mod, chunks, status in rows:
        add_item(iid, src, kind, title, status=status, modified_at=iso(mod))
        ctx.index.replace_item_chunks(iid, src, chunks, emb.embed_documents(chunks))
        if kind == "image":
            ctx.index.set_image(iid, src, emb.embed_documents(["igem poster picture"])[0])
    return ctx


def test_rrf_prefers_items_in_both_lists():
    out = rrf_merge(["a", "b", "c"], ["c", "d"])
    assert out[0][0] == "c" and {k for k, _ in out} == {"a", "b", "c", "d"}


def test_rrf_k60_values():
    (k1, s1), *_ = rrf_merge(["x"])
    assert abs(s1 - 1 / 61) < 1e-12


def test_rrf_ties_sorted_by_key_and_empty():
    assert [k for k, _ in rrf_merge(["b"], ["a"])] == ["a", "b"]
    assert rrf_merge() == [] and rrf_merge([], []) == []


def test_when_bounds(tz_utc):
    now = 1_792_000_000.0
    assert when_bounds("any", now) == (None, None)
    lo, hi = when_bounds("week", now)
    assert lo == iso(now - 7 * DAY) and hi is None
    lo, hi = when_bounds("older", now)
    assert lo is None and hi == iso(now - 365 * DAY)
    # SGT midnight, not the machine's (UTC here) midnight
    lo, _ = when_bounds("today", 1_792_000_000.0)
    from misty_engine.clock import sgt_day_start
    assert lo == iso(sgt_day_start(1_792_000_000.0))


def test_search_filters_source_and_when(indexed_ctx, clock):
    hits = search(indexed_ctx, "iGEM wiki", sources=["files"])
    assert hits and all(h.source in ("folders", "repos") and h.kind != "image" for h in hits)
    week = search(indexed_ctx, "iGEM", when="week")
    assert week and all(h.modified_at >= iso(clock.now() - 7 * DAY) for h in week)
    assert "physics_ia.md" not in [h.title for h in search(indexed_ctx, "physics assessment", when="week")]
    assert [h.title for h in search(indexed_ctx, "physics assessment", when="older")] == ["physics_ia.md"]
    assert "physics_ia.md" not in [h.title for h in search(indexed_ctx, "physics assessment", when="year")]


def test_search_photos_and_mail(indexed_ctx):
    assert {h.kind for h in search(indexed_ctx, "igem", sources=["photos"])} == {"image"}
    assert {h.source for h in search(indexed_ctx, "igem", sources=["mail"])} == {"gmail"}
    both = search(indexed_ctx, "igem", sources=["mail", "photos"])
    assert {h.source for h in both} == {"gmail", "folders"}


def test_search_never_returns_skipped(indexed_ctx):
    assert "igem_copy.md" not in [h.title for h in search(indexed_ctx, "iGEM wiki draft deadline Friday")]


def test_search_snippets_and_locators(indexed_ctx):
    h = search(indexed_ctx, "pendulum")[0]
    assert h.title == "physics_ia.md" and "«pendulum»" in h.snippet and h.locator == "/x/it_0000000000000010"
    img = next(h for h in search(indexed_ctx, "poster", sources=["photos"]))
    assert img.kind == "image"


def test_search_limit_and_scores_descending(indexed_ctx):
    hits = search(indexed_ctx, "iGEM wiki draft", limit=2)
    assert len(hits) == 2 and hits[0].score >= hits[1].score


@pytest.mark.parametrize("q", ["", "   ", "what's up?", '"x', "AND OR", "🏏", "kya scene hai bhai"])
def test_search_messy_input_never_raises(indexed_ctx, q):
    assert isinstance(search(indexed_ctx, q), list)


def test_source_filters_cover_spec_sources():
    assert set(SOURCE_FILTERS) == {"files", "photos", "mail", "calendar", "imessage", "whatsapp", "claude_mem"}


def test_search_latency_budget(ctx, add_item, emb, clock):
    t0 = time.perf_counter()
    n_items = 2000
    for i in range(n_items):
        iid = f"it_{i:016x}"
        add_item(iid, "folders", "file", f"doc{i}.md", status="scrubbed", modified_at=iso(clock.now() - i * 60))
        chunks = [f"paragraph {i}-{j} about topic{(i * 7 + j) % 50} and word{i % 97}" for j in range(10)]
        ctx.index.replace_item_chunks(iid, "folders", chunks, emb.embed_documents(chunks))
    build_s = time.perf_counter() - t0
    t = time.perf_counter()
    hits = search(ctx, "physics internal assessment topic7 draft")
    took = time.perf_counter() - t
    assert hits and took < 0.3, (took, build_s)
