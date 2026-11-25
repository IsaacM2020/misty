import json
import subprocess

import pytest

from misty_engine.cloud import item_text
from misty_engine.errors import Fatal
from misty_engine.pipeline import Worker
from misty_engine.types import Job

LONG = "The iGEM team met about the wiki and the part documentation deadline. " * 12


def status_of(ctx, iid):
    return ctx.con.execute("SELECT status FROM items WHERE id=?", (iid,)).fetchone()[0]


def row(ctx, iid):
    return ctx.con.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()


async def run(ctx, kinds=("worth", "compile_batch", "embed_note")):
    await Worker(ctx, {k: 4 for k in kinds}).run_until_idle(list(kinds))
    assert ctx.queue.counts().get("failed", 0) == 0, "a handler crashed"


def commits(root):
    return int(subprocess.run(["git", "-C", str(root), "rev-list", "--count", "HEAD"],
                              capture_output=True, text=True).stdout)


async def test_worth_no_marks_done_without_note(fctx, fake_or, seed):
    iid = seed("it_aaaa000000000001", LONG)
    fake_or.set_answers({"worth": ("no", 0.95)})
    fctx.queue.enqueue("worth", item_id=iid, cloud=True)
    await run(fctx)
    assert status_of(fctx, iid) == "done" and row(fctx, iid)["note_path"] is None and row(fctx, iid)["matters"] == 0


async def test_worth_unsure_keeps_item(fctx, fake_or, seed):
    iid = seed("it_aaaa000000000002", LONG)
    fake_or.set_answers({"worth": ("no", 0.55)})
    fctx.queue.enqueue("worth", item_id=iid, cloud=True)
    await run(fctx)
    assert status_of(fctx, iid) == "triaged"


async def test_compile_batch_writes_notes_and_one_commit(fctx, fake_or, seed):
    ids = [seed(f"it_bbbb00000000000{i}", LONG, title=f"note{i}.md") for i in range(8)]
    for i in ids:
        fctx.con.execute("UPDATE items SET status='triaged', matters=1 WHERE id=?", (i,))
    before = commits(fctx.paths.wiki)
    fctx.queue.enqueue("compile_batch", payload={"item_ids": ids, "build_id": "bd_000000000001"}, cloud=True)
    await run(fctx)
    assert all(status_of(fctx, i) == "embedded" for i in ids)
    assert commits(fctx.paths.wiki) == before + 1
    r = row(fctx, ids[0])
    assert r["note_path"].startswith("sources/folders/") and fctx.wiki.exists(r["note_path"])
    assert fctx.con.execute("select count(*) from vec_refs where ref_kind='page'").fetchone()[0] == 8
    assert "compile: 8 source notes" in (fctx.paths.wiki / "log.md").read_text()


async def test_compile_stores_draft_metadata(fctx, fake_or, seed):
    iid = seed("it_cccc000000000001", LONG)
    fctx.con.execute("UPDATE items SET status='triaged' WHERE id=?", (iid,))
    fake_or.set_draft(iid, people=["Aarav Shah"], projects=["iGEM wiki"],
                      dates=[{"date": "2026-10-16", "line": "Wiki sync"}])
    fctx.queue.enqueue("compile_batch", payload={"item_ids": [iid]}, cloud=True)
    await run(fctx)
    meta = json.loads(row(fctx, iid)["meta"])
    assert meta["people"] == ["Aarav Shah"] and meta["dated_lines"] == [["2026-10-16", "Wiki sync"]]


async def test_compile_bad_json_retries_once_then_compile_failed(fctx, fake_or, seed):
    iid = seed("it_dddd000000000001", LONG)
    fctx.con.execute("UPDATE items SET status='triaged' WHERE id=?", (iid,))
    fake_or.chat_returns("not json at all", times=2)
    fctx.queue.enqueue("compile_batch", payload={"item_ids": [iid]}, cloud=True)
    await run(fctx)
    r = row(fctx, iid)
    assert r["status"] == "compile_failed" and r["error_code"] == "JSON_PARSE"
    assert len(fake_or.chat_bodies) == 2


async def test_compile_cut_off_at_output_cap_is_marked_truncated(fctx, fake_or, seed):
    iid = seed("it_dddd000000000002", LONG)
    fctx.con.execute("UPDATE items SET status='triaged' WHERE id=?", (iid,))
    fake_or.finish_reason = "length"
    fake_or.chat_returns('{"notes": [{"item_id": "it_dddd000000000002", "title": "Cut', times=2)
    fctx.queue.enqueue("compile_batch", payload={"item_ids": [iid]}, cloud=True)
    await run(fctx)
    r = row(fctx, iid)
    assert r["status"] == "compile_failed" and r["error_code"] == "TRUNCATED"


async def test_compile_failed_item_is_still_searchable(fctx, fake_or, seed):
    from misty_engine.search import search
    iid = seed("it_eeee000000000001", "pendulum experiment results", title="physics.md")
    fctx.con.execute("UPDATE items SET status='triaged' WHERE id=?", (iid,))
    fake_or.chat_returns("garbage", times=2)
    fctx.queue.enqueue("compile_batch", payload={"item_ids": [iid]}, cloud=True)
    await run(fctx)
    assert status_of(fctx, iid) == "compile_failed"
    assert any(h.item_id == iid for h in search(fctx, "pendulum experiment"))


async def test_cap_hit_pauses_cloud_jobs_and_not_local(fctx, fake_or, seed):
    iid = seed("it_ffff000000000001", LONG)
    fctx.con.execute("UPDATE items SET status='triaged' WHERE id=?", (iid,))
    fctx.settings.set("cap_build_usd", 0.0000001)
    jid = fctx.queue.enqueue("compile_batch", payload={"item_ids": [iid], "build_id": "bd_000000000001"}, cloud=True)
    fctx.queue.enqueue("embed_note", item_id=seed("it_ffff000000000002", LONG))   # a local job is unaffected
    await run(fctx)
    assert fctx.queue.get(jid).status == "waiting_cap"
    assert fake_or.request_count == 0


async def test_network_drop_queues_then_resumes(fctx, fake_or, seed):
    iid = seed("it_9999000000000001", LONG)
    fctx.con.execute("UPDATE items SET status='triaged' WHERE id=?", (iid,))
    fctx.net.set_online(False, "test")
    jid = fctx.queue.enqueue("compile_batch", payload={"item_ids": [iid]}, cloud=True)
    await run(fctx)
    assert fctx.queue.get(jid).status == "waiting_network" and status_of(fctx, iid) == "triaged"
    fctx.net.set_online(True, "test")
    fctx.queue.release("waiting_network")
    await run(fctx)
    assert status_of(fctx, iid) == "embedded"


async def test_paid_lock_blocks_real_host(fctx, seed, settings):
    from misty_engine.openrouter import OpenRouterClient
    real = OpenRouterClient(pinned=fctx.pinned, settings=settings, spend=fctx.spend, net=fctx.net,
                            key_provider=lambda: "sk-or-v1-x", clock=fctx.clock)
    with pytest.raises(Fatal) as e:
        await real.chat(model_key="haiku", messages=[{"role": "user", "content": "hi"}], max_tokens=5,
                        job_id=None, phase="live")
    assert e.value.code == "PAID_LOCKED"


def test_item_text_dejoins_overlap(fctx, seed):
    body = "\n\n".join(f"Paragraph {i}. " + "alpha beta gamma " * 60 for i in range(6))
    iid = seed("it_8888000000000001", body, title="long.md")
    txt = item_text(fctx, iid)
    assert txt.count("Paragraph 3.") == 1 and "long.md" in txt


async def test_mail_kept_by_the_allowlist_skips_the_worth_gate(fctx, fake_or, seed):
    iid = seed("it_cccc000000000001", "New assignment: Worksheets for Elasticity. Due Friday. " * 6,
               source="gmail", kind="mail_thread", title="New assignment")
    fctx.con.execute("UPDATE items SET meta=? WHERE id=?", (json.dumps({"keep_rule": "allowlist"}), iid))
    fake_or.set_answers({"worth": ("no", 0.95)})            # Jev would have dropped it as a notification
    fctx.queue.enqueue("worth", item_id=iid, cloud=True)
    await run(fctx)
    assert status_of(fctx, iid) in ("triaged", "embedded") and row(fctx, iid)["matters"] == 1
    assert fake_or.systemone_bodies == []


async def test_mail_kept_only_because_it_looks_human_still_faces_the_worth_gate(fctx, fake_or, seed):
    iid = seed("it_cccc000000000002", "Your project has been paused. " * 8, source="gmail", kind="mail_thread",
               title="Paused")
    fctx.con.execute("UPDATE items SET meta=? WHERE id=?", (json.dumps({"keep_rule": "human"}), iid))
    fake_or.set_answers({"worth": ("no", 0.95)})
    fctx.queue.enqueue("worth", item_id=iid, cloud=True)
    await run(fctx)
    assert status_of(fctx, iid) == "done" and len(fake_or.systemone_bodies) == 1


def test_recompile_reuses_the_items_note_path(fctx, seed):
    """A new title must not leave the old note file behind (orphan, and timeline links would point at it)."""
    from misty_engine.cloud import store_notes
    from misty_engine.compile.haiku import NoteDraft
    iid = seed("it_cccc000000000001", "words " * 60, source="gmail", kind="mail_thread", title="Subject")
    by_id = lambda: {iid: fctx.con.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()}   # noqa: E731
    first = store_notes(fctx, by_id(), {iid: NoteDraft(item_id=iid, title="First title", summary="s")})
    second = store_notes(fctx, by_id(), {iid: NoteDraft(item_id=iid, title="A completely different title",
                                                       summary="s")})
    assert second == first
    notes = [p for p in fctx.wiki.all_pages() if p.startswith("sources/")]
    assert notes == first
    page = fctx.wiki.read(first[0])
    assert page.fm["title"] == "A completely different title"


async def test_embed_note_does_not_enqueue_link(fctx, fake_or, seed):
    iid = seed("it_eeee000000000001", LONG)
    fctx.con.execute("UPDATE items SET status='triaged', matters=1 WHERE id=?", (iid,))
    fctx.queue.enqueue("compile_batch", payload={"item_ids": [iid]}, cloud=True)
    await run(fctx)
    kinds = {r["kind"] for r in fctx.con.execute("SELECT kind FROM jobs")}
    assert "link" not in kinds and status_of(fctx, iid) == "embedded"
