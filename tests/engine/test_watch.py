import asyncio
import time
from pathlib import Path

import pytest

from misty_engine.clock import Clock
from misty_engine.pipeline import Worker, upsert_item
from misty_engine.sources.folders import FolderSource
from misty_engine.watch import ChatDbWatcher, Debouncer, FolderWatcher, ignorable


def test_debouncer_collapses_editor_save_bursts(clock):
    d = Debouncer(2, clock)
    for _ in range(10):
        d.touch("/a.md")
        clock.advance(0.1)
    assert d.due() == []
    clock.advance(2.1)
    assert d.due() == ["/a.md"] and d.due() == []


def test_ignorable_paths():
    for p in ("/h/Desktop/~$essay.docx", "/h/x/.a.md.swp", "/h/x/file.tmp", "/h/x/.DS_Store",
              "/h/proj/node_modules/x.md", "/h/proj/.git/config", "/h/Desktop/.old.pdf.icloud"):
        assert ignorable(p), p
    assert not ignorable("/h/Desktop/essay.docx")


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


def test_folder_event_reaches_flush_and_ignores_excluded_and_temp(tmp_path):
    root = tmp_path / "home"
    (root / "Desktop").mkdir(parents=True)
    (root / "secret").mkdir()
    w = FolderWatcher([root], [root / "secret"], delay_s=0.3, clock=Clock())
    w.start()
    try:
        time.sleep(0.5)
        (root / "Desktop/new.md").write_text("hello " * 100)
        (root / "Desktop/~$new.docx").write_text("lock")
        (root / "Desktop/.new.md.swp").write_text("swap")
        (root / "secret/hidden.md").write_text("no")
        got: list[Path] = []

        def poll():
            got.extend(w.flush())
            return any(p.name == "new.md" for p in got)
        assert wait_for(poll)
        names = {p.name for p in got}
        assert names == {"new.md"}
    finally:
        w.stop()


def test_chatdb_wal_change_triggers_source(tmp_path):
    db = tmp_path / "chat.db"
    db.write_text("x")
    w = ChatDbWatcher(db, "imessage", delay_s=0.3, clock=Clock())
    w.start()
    try:
        time.sleep(0.5)
        (tmp_path / "chat.db-wal").write_text("frame")
        (tmp_path / "other.txt").write_text("nope")
        got: list[str] = []
        assert wait_for(lambda: got.extend(w.flush()) or got)
        assert got == ["imessage"]
    finally:
        w.stop()


def test_chatdb_shm_change_is_ignored(tmp_path):
    """Our own read-only reads write chat.db-shm; watching it made discover re-trigger itself every ~6 s."""
    db = tmp_path / "chat.db"
    db.write_text("x")
    w = ChatDbWatcher(db, "imessage", delay_s=0.3, clock=Clock())
    w._on_event(str(tmp_path / "chat.db-shm"))
    time.sleep(0.4)
    assert w.flush() == []
    w._on_event(str(tmp_path / "chat.db-wal"))
    time.sleep(0.4)
    assert w.flush() == ["imessage"]


async def test_discover_paths_upserts_and_preps_then_delete_removes_from_search(fctx, tmp_path):
    from misty_engine.search import search
    root = tmp_path / "home"
    (root / "Desktop").mkdir(parents=True)
    f = root / "Desktop/new.md"
    f.write_text("pendulum experiment period results " * 100)
    fctx.sources = {"folders": FolderSource([root], [])}
    fctx.queue.enqueue("discover_paths", payload={"paths": [str(f)]})
    await Worker(fctx, {"discover_paths": 1, "prep": 1}).run_until_idle(["discover_paths", "prep"])
    assert fctx.con.execute("select status from items where locator=?", (str(f),)).fetchone()[0] == "scrubbed"
    assert search(fctx, "pendulum experiment")
    f.unlink()
    fctx.queue.enqueue("discover_paths", payload={"paths": [str(f)]})
    await Worker(fctx, {"discover_paths": 1}).run_until_idle(["discover_paths"])
    r = fctx.con.execute("select status, error_code from items where locator=?", (str(f),)).fetchone()
    assert (r["status"], r["error_code"]) == ("skipped", "DELETED")
    assert not search(fctx, "pendulum experiment")


async def test_discover_paths_ignores_unsupported_and_excluded(fctx, tmp_path):
    root = tmp_path / "home"
    (root / "Desktop").mkdir(parents=True)
    (root / "x").mkdir()
    (root / "Desktop/code.py").write_text("print(1)")
    (root / "x/a.md").write_text("excluded " * 50)
    fctx.sources = {"folders": FolderSource([root], [root / "x"])}
    fctx.queue.enqueue("discover_paths", payload={"paths": [str(root / "Desktop/code.py"), str(root / "x/a.md")]})
    await Worker(fctx, {"discover_paths": 1}).run_until_idle(["discover_paths"])
    assert fctx.con.execute("select count(*) from items").fetchone()[0] == 0
