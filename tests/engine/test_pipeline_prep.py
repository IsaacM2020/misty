import os

from misty_engine.pipeline import (Worker, handler, next_after_prep, scrub_context, upsert_item, index_report,
                                   REGISTRY)
from misty_engine.sources.folders import FolderSource
from misty_engine.types import RawItem


async def _index(ctx, root, images=False, **extra):
    ctx.sources = {"folders": FolderSource([root], [], include_images=images)}
    ctx.queue.enqueue("discover", payload={"source": "folders"})
    await Worker(ctx, {"discover": 1, "prep": 4, **extra}).run_until_idle()


async def test_index_tree_end_to_end(ctx, tree):
    await _index(ctx, tree / "Desktop")
    rows = dict(ctx.con.execute("select title, status from items").fetchall())
    assert "photo.heic" not in rows                  # P26: no images in V1 unless asked
    await _index(ctx, tree / "Desktop", images=True)
    rows = dict(ctx.con.execute("select title, status from items").fetchall())
    assert rows["essay.pdf"] == "scrubbed"          # >300 words, waits for the build
    assert rows["short.md"] == "done"                # <300 words, no LLM
    assert rows[".env"] == "done" and rows["photo.heic"] == "done"
    assert ctx.con.execute("select secret_file from items where title='.env'").fetchone()[0] == 1


async def test_prep_never_stores_secrets(ctx, tree):
    (tree / "Desktop/keys.md").write_text("notes\npassword: Hunter2Canary!\n" + "filler " * 400)
    await _index(ctx, tree / "Desktop")
    dump = "\n".join(r[0] for r in ctx.con.execute("select text_scrubbed from item_chunks"))
    assert "Hunter2Canary!" not in dump and "[REDACTED:password]" in dump


async def test_secret_file_is_indexed_by_name_only(ctx, tree):
    (tree / "Desktop/.env").write_text("OPENAI=sk-or-v1-" + "a" * 40 + "\nCanaryValue123\n")
    await _index(ctx, tree / "Desktop")
    dump = "\n".join(r[0] for r in ctx.con.execute("select text_scrubbed from item_chunks"))
    assert "CanaryValue123" not in dump and "sk-or-v1" not in dump
    assert any(h.ref_id.startswith(ctx.con.execute("select id from items where title='.env'").fetchone()[0])
               for h in ctx.index.fts_search("env"))


async def test_prep_bad_file_marks_skipped(ctx, tree):                  # Review Focus 3
    (tree / "Desktop/broken.pdf").write_bytes(b"%PDF-1.4 garbage")
    await _index(ctx, tree / "Desktop")
    st, code = ctx.con.execute("select status, error_code from items where title='broken.pdf'").fetchone()
    assert (st, code) == ("skipped", "EXTRACT_FAILED")
    assert ctx.con.execute("select count(*) from jobs where status='failed'").fetchone()[0] == 0


async def test_file_vanished_between_discover_and_prep(ctx, tree):
    ctx.sources = {"folders": FolderSource([tree / "Desktop"], [])}
    ctx.queue.enqueue("discover", payload={"source": "folders"})
    await Worker(ctx, {"discover": 1}).run_until_idle(["discover"])
    (tree / "Desktop/short.md").unlink()
    await Worker(ctx, {"prep": 4}).run_until_idle(["prep"])
    st, code = ctx.con.execute("select status, error_code from items where title='short.md'").fetchone()
    assert (st, code) == ("skipped", "FILE_GONE")


async def test_unchanged_file_not_reprocessed(ctx, tree):
    await _index(ctx, tree / "Desktop")
    before = ctx.con.execute("select count(*) from jobs where kind='prep'").fetchone()[0]
    await _index(ctx, tree / "Desktop")
    assert ctx.con.execute("select count(*) from jobs where kind='prep'").fetchone()[0] == before


async def test_changed_file_is_reprocessed_and_old_chunks_replaced(ctx, tree):
    await _index(ctx, tree / "Desktop")
    p = tree / "Desktop/short.md"
    p.write_text("a brand new sentence about robotics\n")
    os.utime(p, (1_900_000_000, 1_900_000_000))
    await _index(ctx, tree / "Desktop")
    assert ctx.index.fts_search("robotics") and not ctx.index.fts_search("nets")


async def test_touched_but_identical_file_keeps_status(ctx, tree):
    await _index(ctx, tree / "Desktop")
    p = tree / "Desktop/essay.pdf"
    os.utime(p, (1_900_000_000, 1_900_000_000))
    ctx.con.execute("update items set status='compiled' where title='essay.pdf'")    # as if M3 compiled it
    await _index(ctx, tree / "Desktop")
    assert ctx.con.execute("select status from items where title='essay.pdf'").fetchone()[0] == "compiled"


async def test_item_left_in_found_is_picked_up_again(ctx, tree):          # crash between discover and prep
    ctx.sources = {"folders": FolderSource([tree / "Desktop"], [])}
    ctx.queue.enqueue("discover", payload={"source": "folders"})
    await Worker(ctx, {"discover": 1}).run_until_idle(["discover"])
    ctx.con.execute("delete from jobs where kind='prep'")                  # the queue lost them
    await _index(ctx, tree / "Desktop")
    assert ctx.con.execute("select count(*) from items where status='found'").fetchone()[0] == 0


async def test_near_duplicate_is_searchable_but_not_compiled(ctx, tree):
    (tree / "Desktop/a.md").write_text("same body " * 400)
    (tree / "Desktop/b.md").write_text("same body " * 400)
    await _index(ctx, tree / "Desktop")
    st = sorted(r[0] for r in ctx.con.execute("select status from items where title in ('a.md','b.md')"))
    assert st == ["done", "scrubbed"]                 # one waits for the build, the copy never will
    dup = ctx.con.execute("select id, meta from items where status='done' and title like '_.md'").fetchone()
    assert "dup_of" in dup[1]
    assert {h.ref_id.rsplit(":", 1)[0] for h in ctx.index.fts_search("same body")} >= {dup[0]}   # still findable
    assert index_report(ctx)["dupes"] == 1


async def test_prep_logs_hold_no_content(ctx, tree, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="misty")
    (tree / "Desktop/story.md").write_text("zebra-moonlight-canary " * 5)
    await _index(ctx, tree / "Desktop")
    assert "zebra" not in caplog.text and "story" not in caplog.text


async def test_unexpected_exception_fails_job_and_others_continue(ctx, tree):
    calls = []

    @handler("boom")
    async def _boom(job, c):
        calls.append(job.id)
        raise RuntimeError("secret detail must not leak")

    try:
        ctx.queue.enqueue("boom", payload={})
        await _index(ctx, tree / "Desktop", boom=1)
        st, code = ctx.con.execute("select status, error_code from jobs where kind='boom'").fetchone()
        assert (st, code) == ("failed", "UNEXPECTED")
        assert ctx.con.execute("select count(*) from items where status='scrubbed'").fetchone()[0] >= 1
    finally:
        REGISTRY.pop("boom", None)


async def test_paused_does_nothing(ctx, tree):
    ctx.settings.set("paused", True)
    await _index(ctx, tree / "Desktop")
    assert ctx.con.execute("select count(*) from items").fetchone()[0] == 0


async def test_index_report(ctx, tree):
    await _index(ctx, tree / "Desktop")
    r = index_report(ctx)
    assert r["status"]["scrubbed"] >= 1 and r["stats"]["folders"]["dataless"] == 1


def test_scrub_context_mapping():
    assert scrub_context("gmail") == "mail"
    assert scrub_context("imessage") == scrub_context("whatsapp") == "message"
    assert scrub_context("folders") == scrub_context("claude_mem") == "file"


def _row(**kw):
    base = dict(id="it_1", source="folders", kind="file", word_count=1000, secret_file=0)
    base.update(kw)
    return base


def test_next_after_prep_rules(con, clock):
    def run(phase="build", **kw):
        r = _row(**kw)
        con.execute("insert or replace into items(id, source, kind, locator, status, updated_at, word_count, secret_file) "
                    "values (?,?,?,?, 'scrubbed', 0, ?, ?)", (r["id"], r["source"], r["kind"], "/l", r["word_count"], r["secret_file"]))
        nxt = next_after_prep(con, r, phase)
        return nxt, con.execute("select status from items where id=?", (r["id"],)).fetchone()[0]

    assert run(kind="secret_file", secret_file=1) == (None, "done")
    assert run(kind="image") == (None, "done")
    assert run(source="claude_mem", kind="mem_obs") == (None, "done")
    assert run(word_count=120) == (None, "done")
    assert run() == (None, "scrubbed")                       # build phase: the orchestrator picks it up
    assert run(phase="live") == ("triage", "scrubbed")
    assert run(phase="live", word_count=120) == (None, "done")


def test_upsert_item_new_changed_unchanged(con, clock):
    raw = RawItem(source="folders", locator="/a.md", kind="file", title="a.md", modified_at="2026-01-01T00:00:00Z",
                  meta={"size": 5, "mtime": 1.0})
    assert upsert_item(con, raw, clock.now()) is True
    con.execute("update items set status='done'")
    assert upsert_item(con, raw, clock.now()) is False
    changed = RawItem(source="folders", locator="/a.md", kind="file", title="a.md", meta={"size": 6, "mtime": 1.0})
    assert upsert_item(con, changed, clock.now()) is True


async def test_run_index_uses_settings(ctx, tree):
    from misty_engine.pipeline import format_report, run_index
    ctx.settings.set("folders", [str(tree / "Desktop")])
    ctx.settings.set("excluded_paths", [])
    ctx.settings.set("sources_enabled", {"folders": True})
    rep = await run_index(ctx)
    assert rep["status"]["scrubbed"] >= 1 and "wall time" in format_report(rep)
    rep2 = await run_index(ctx, source="folders", limit=1)
    assert rep2["status"]["scrubbed"] >= 1
