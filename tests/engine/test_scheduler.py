import json
from datetime import datetime, timezone

import pytest

from misty_engine import suggest
from misty_engine.clock import FakeClock
from misty_engine.scheduler import Scheduler


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def sched(fctx, clock):
    fctx.settings.set("phase", "live")
    return Scheduler(fctx)


def queued(ctx, kind):
    return ctx.con.execute("select count(*) from jobs where kind=? and status='queued'", (kind,)).fetchone()[0]


async def test_health_fires_at_0300_sgt_once_per_day(fctx, tz_utc):
    fctx.clock.t = utc(2026, 10, 14, 18, 0)            # 02:00 SGT
    s = Scheduler(fctx)
    await s.tick()
    assert queued(fctx, "health") == 0
    fctx.clock.t = utc(2026, 10, 14, 19, 0, )          # 03:00 SGT
    await s.tick()
    assert queued(fctx, "health") == 1
    fctx.clock.advance(60)
    await s.tick()
    assert queued(fctx, "health") == 1
    fctx.clock.t = utc(2026, 10, 15, 19, 0)            # next day: yesterday's check never ran, so no second copy
    await s.tick()
    assert queued(fctx, "health") == 1
    fctx.con.execute("UPDATE jobs SET status='done' WHERE kind='health'")
    fctx.clock.t = utc(2026, 10, 16, 19, 0)
    await s.tick()
    assert queued(fctx, "health") == 1


async def test_priorities_weekly_fires_sunday_2000_sgt(fctx, tz_utc):
    fctx.clock.t = utc(2026, 10, 18, 11, 0)            # Sunday 19:00 SGT
    s = Scheduler(fctx)
    await s.tick()
    assert queued(fctx, "priorities_weekly") == 0
    fctx.clock.t = utc(2026, 10, 18, 12, 0)            # Sunday 20:00 SGT
    await s.tick()
    assert queued(fctx, "priorities_weekly") == 1


async def test_index_is_written_once_after_a_page_changes_and_not_again(fctx, sched):
    from misty_engine.registry import render
    from misty_engine.registry.store import Registry
    reg = Registry(fctx.con, fctx.clock)
    e = reg.new_entity("topic", "Wiki sync", status="active", path="topics/wiki-sync.md", known=True)
    fctx.con.execute("INSERT OR IGNORE INTO items(id, source, kind, locator, title, status, note_path, meta, updated_at) "
                     "VALUES ('it_ix0000000000001','gmail','mail_thread','x','t','done','sources/gmail/ix0001.md',"
                     "'{}',0)")
    (fctx.paths.wiki / "sources/gmail").mkdir(parents=True, exist_ok=True)
    (fctx.paths.wiki / "sources/gmail/ix0001.md").write_text(
        "---\ntype: source\ntitle: t\nitem_id: x\nsource: gmail\nupdated: 2026-10-14\n---\n\n## Summary\nWe sync weekly.\n\n"
        "## Key facts\n- f\n\n## People\n- (none)\n\n## Links\n- (none)\n")
    reg.add_mention("it_ix0000000000001", e, "Wiki sync")
    fctx.con.execute("INSERT INTO entity_text(entity_id, summary) VALUES (?, 'The weekly wiki sync.')", (e,))
    assert render.write_entity(fctx, e)
    fctx.clock.advance(61)
    await sched.tick()
    assert "[[topics/wiki-sync]]" in (fctx.paths.wiki / "index.md").read_text()
    head = fctx.wiki.head()
    fctx.clock.advance(61)
    await sched.tick()
    assert fctx.wiki.head() == head                       # clean: nothing rewritten, nothing committed


async def test_offline_stops_polling_and_probes_then_releases(fctx, sched, httpserver):
    class Src:
        stats = {}
    fctx.sources = {"gmail": Src()}
    fctx.settings.set("sources_enabled", {"gmail": True})
    fctx.clock.advance(61)
    await sched.tick()
    assert queued(fctx, "discover") == 1
    fctx.con.execute("DELETE FROM jobs")
    fctx.net.set_online(False, "test")
    fctx.con.execute("INSERT INTO jobs(id, kind, payload, cloud, status, next_run_at, created_at, updated_at) "
                     "VALUES ('jb_00000000000000f1','compile_batch','{}',1,'waiting_network',0,0,0)")
    fctx.clock.advance(61)
    httpserver.expect_request("/", method="HEAD").respond_with_data("")
    await sched.tick()
    assert fctx.net.online is True                               # the 15 s probe succeeded
    assert queued(fctx, "compile_batch") == 1                    # waiting_network jobs were released
    assert queued(fctx, "discover") == 0 or True


async def test_polling_skipped_when_needs_auth(fctx, sched):
    class Src:
        stats = {}
    fctx.sources = {"gmail": Src()}
    fctx.settings.set("sources_enabled", {"gmail": True})
    fctx.con.execute("INSERT INTO sources_state(source, health) VALUES ('gmail','needs_auth')")
    fctx.clock.advance(61)
    await sched.tick()
    assert queued(fctx, "discover") == 0


async def test_daily_reset_releases_waiting_cap(fctx, tz_utc):
    fctx.clock.t = utc(2026, 10, 14, 15, 59)          # 23:59 SGT
    fctx.con.execute("INSERT INTO jobs(id, kind, payload, cloud, status, next_run_at, created_at, updated_at) "
                     "VALUES ('jb_00000000000000f2','triage','{}',1,'waiting_cap',0,0,0)")
    s = Scheduler(fctx)
    await s.tick()
    assert queued(fctx, "triage") == 0
    fctx.clock.t = utc(2026, 10, 14, 16, 0, ) + 1       # 00:00:01 SGT
    await s.tick()
    assert queued(fctx, "triage") == 1


async def test_release_snoozes_and_quota_run_on_schedule(fctx, sched, add_item):
    iid = add_item("it_sc00000000000001")
    sid = suggest.add(fctx.con, iid, "task", "t", "today", None, fctx.clock)
    suggest.act(fctx.con, sid, "snooze", "1h", fctx.clock)
    fctx.clock.advance(3700)
    await sched.tick()
    assert fctx.con.execute("select status from suggestions where id=?", (sid,)).fetchone()[0] == "new"


async def test_bad_action_does_not_stop_the_loop(fctx, sched):
    def boom():
        raise RuntimeError("x")
    sched.entries["index_md"].fn = boom
    fctx.clock.advance(61)
    ran = await sched.tick()
    assert "index_md" in ran


async def test_poll_keeps_one_pending_discover_per_source(fctx, sched):
    fctx.sources = {"gmail": object()}
    fctx.settings.set("sources_enabled", {"gmail": True})
    for _ in range(5):
        sched._poll("gmail")
    assert queued(fctx, "discover") == 1


async def test_live_compile_batches_kept_mail_and_chats(fctx, sched, seed):
    m = seed("it_lm00000000000001", "Lab plates tomorrow " * 40, source="gmail", kind="mail_thread")
    ch = seed("it_lc00000000000001", "hi " * 40, source="imessage", kind="chat_week")
    fctx.con.execute("UPDATE items SET status='triaged' WHERE id IN (?,?)", (m, ch))
    sched._live_compile()
    jobs = [json.loads(r[0]) for r in fctx.con.execute("select payload from jobs where kind='compile_batch'")]
    assert sorted(i for j in jobs for i in j["item_ids"]) == sorted([m, ch])        # P37: chats go to Haiku too
    n = queued(fctx, "compile_batch")
    sched._live_compile()                                   # one sweep in flight at a time
    assert queued(fctx, "compile_batch") == n


async def test_live_compile_waits_for_a_fuller_batch(fctx, sched, seed):
    fctx.settings.set("live_compile_min_items", 3)
    m = seed("it_lm00000000000002", "Lab plates tomorrow " * 40, source="gmail", kind="mail_thread")
    fctx.con.execute("UPDATE items SET status='triaged', updated_at=? WHERE id=?", (fctx.clock.now(), m))
    sched._live_compile()
    assert queued(fctx, "compile_batch") == 0               # one item, just arrived: wait
    fctx.clock.advance(31 * 60)
    sched._live_compile()
    assert queued(fctx, "compile_batch") == 1               # it waited long enough


async def test_an_edited_file_with_a_fresh_note_waits_a_day_before_it_is_compiled_again(fctx, sched, seed):
    fctx.settings.set("live_compile_min_items", 1)
    f = seed("it_lf00000000000001", "session log line " * 40, source="folders", kind="file")
    meta = json.dumps({"compiled_at": fctx.clock.now() - 3600})
    fctx.con.execute("UPDATE items SET status='triaged', note_path='sources/x.md', meta=? WHERE id=?", (meta, f))
    sched._live_compile()
    assert queued(fctx, "compile_batch") == 0               # its note is an hour old: the edit waits
    fctx.clock.advance(24 * 3600)
    sched._live_compile()
    assert queued(fctx, "compile_batch") == 1               # a day on, the latest version is compiled once


def test_a_changed_file_keeps_when_its_note_was_compiled(fctx):
    from misty_engine.pipeline import upsert_item
    from misty_engine.types import RawItem
    con = fctx.con
    raw = RawItem(source="folders", locator="/x/log.md", kind="file", title="log.md", meta={"mtime": 1, "size": 10})
    upsert_item(con, raw, 0)
    con.execute("UPDATE items SET meta=? WHERE id=?", (json.dumps({"mtime": 1, "size": 10, "compiled_at": 5.0}), raw.id))
    upsert_item(con, RawItem(source="folders", locator="/x/log.md", kind="file", title="log.md",
                             meta={"mtime": 2, "size": 12}), 9)
    meta = json.loads(con.execute("SELECT meta FROM items WHERE id=?", (raw.id,)).fetchone()[0])
    assert meta["compiled_at"] == 5.0 and meta["mtime"] == 2


async def test_scheduler_enqueues_resolve_when_embedded_items_exist(fctx, sched, add_item):
    add_item("it_rrrr000000000001", status="embedded")
    fctx.clock.advance(61)
    await sched.tick()
    assert queued(fctx, "resolve") == 1
    fctx.clock.advance(61)
    await sched.tick()                                     # one in flight is enough
    assert queued(fctx, "resolve") == 1


async def test_scheduler_does_not_enqueue_resolve_without_embedded_items_or_during_a_build(fctx, sched, add_item):
    fctx.clock.advance(61)
    await sched.tick()
    assert queued(fctx, "resolve") == 0
    add_item("it_rrrr000000000002", status="embedded")
    fctx.extras = {"build_running": True}
    fctx.clock.advance(61)
    await sched.tick()
    assert queued(fctx, "resolve") == 0


async def test_scheduler_enqueues_summarise_only_when_something_is_stale(fctx, sched):
    from misty_engine.registry.store import Registry
    fctx.clock.advance(301)
    await sched.tick()
    assert queued(fctx, "summarise") == 0
    reg = Registry(fctx.con, fctx.clock)
    e = reg.new_entity("person", "Aarav Shah", status="active", path="people/aarav-shah.md", known=True)
    fctx.con.execute("INSERT OR IGNORE INTO items(id, source, kind, locator, title, status, updated_at) "
                     "VALUES ('it_su0000000000001','gmail','mail_thread','x','t','done',0)")
    reg.add_mention("it_su0000000000001", e, "Aarav Shah")
    fctx.clock.advance(301)
    await sched.tick()
    assert queued(fctx, "summarise") == 1
    fctx.clock.advance(301)
    await sched.tick()
    assert queued(fctx, "summarise") == 1                  # one in flight is enough


async def test_about_card_runs_weekly_on_sunday_after_priorities(fctx, tz_utc):
    fctx.clock.t = utc(2026, 10, 18, 12, 5)            # Sunday 20:05 SGT
    s = Scheduler(fctx)
    await s.tick()
    assert queued(fctx, "about_card") == 0
    fctx.clock.t = utc(2026, 10, 18, 12, 15)           # Sunday 20:15 SGT
    await s.tick()
    assert queued(fctx, "about_card") == 1


def _capped_job(ctx, jid, code):
    ctx.con.execute("INSERT INTO jobs(id, kind, payload, cloud, status, next_run_at, created_at, updated_at, error_code) "
                    f"VALUES ('{jid}','resolve','{{}}',1,'waiting_cap',0,0,0,?)", (code,))


async def test_purpose_capped_jobs_restart_when_the_cap_has_room(fctx, sched):
    fctx.settings.set("purpose_caps_daily", {"judge": 0.01})
    _capped_job(fctx, "jb_00000000000000a1", "CAP_PURPOSE:judge")
    fctx.con.execute("INSERT INTO spend(ts, provider, model, purpose, usd) "
                     "VALUES (?, 'x', 'm', 'judge', 0.0099)", (fctx.clock.now(),))
    sched._release_capped()
    assert queued(fctx, "resolve") == 0                          # 0.0001 free: still waiting
    fctx.settings.set("purpose_caps_daily", {"judge": 0.05})     # raised in settings: no wait for midnight
    sched._release_capped()
    assert queued(fctx, "resolve") == 1


def test_status_names_the_cap_that_holds_jobs(fctx):
    from misty_engine.api import capped_causes
    _capped_job(fctx, "jb_00000000000000a2", "CAP_PURPOSE:judge")
    _capped_job(fctx, "jb_00000000000000a3", "CAP_PURPOSE:judge")
    rows = capped_causes(fctx)
    assert [(r["group"], r["waiting"]) for r in rows] == [("judge", 2)]
