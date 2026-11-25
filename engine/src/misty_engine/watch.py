"""File and chat-database watchers (FSEvents). Threads only record paths; the loop thread does all DB work."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from .clock import Clock
from .ctx import Ctx
from .logs import log_event
from .pipeline import handler, source_for, upsert_item
from .sources.repos import SKIP_DIRS
from .types import Job

TEMP_SUFFIXES = (".swp", ".swx", ".tmp", ".crdownload", ".part", "~")


class Debouncer:
    """Collapses bursts (editor saves) into one event per key after `delay_s` of quiet."""

    def __init__(self, delay_s: float, clock: Clock):
        self.delay_s, self.clock = delay_s, clock
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def touch(self, key: str) -> None:
        with self._lock:
            self._last[key] = self.clock.now()

    def due(self) -> list[str]:
        now = self.clock.now()
        with self._lock:
            ready = [k for k, t in self._last.items() if now - t >= self.delay_s]
            for k in ready:
                del self._last[k]
        return sorted(ready)


def ignorable(path: str) -> bool:
    p = Path(path)
    name = p.name
    if name.startswith("~$") or name.endswith(TEMP_SUFFIXES) or name == ".DS_Store" or name.endswith(".icloud"):
        return True
    return any(part in SKIP_DIRS or (part.startswith(".") and part not in (".", "..")) for part in p.parts[1:])


class _Handler:
    def __init__(self, cb: Callable[[str], None]):
        self.cb = cb

    def dispatch(self, event) -> None:
        for attr in ("src_path", "dest_path"):
            path = getattr(event, attr, "")
            if path and not getattr(event, "is_directory", False):
                self.cb(os.fsdecode(path))


class FolderWatcher:
    def __init__(self, roots: list[Path], excluded: list[Path], *, delay_s: float = 2.0, clock: Clock | None = None):
        self.roots = [Path(os.path.realpath(r)) for r in roots]
        self.excluded = [Path(os.path.realpath(e)) for e in excluded]
        self.deb = Debouncer(delay_s, clock or Clock())
        self._obs = None

    def _excluded(self, path: str) -> bool:
        p = Path(path)
        return any(p == e or e in p.parents for e in self.excluded)

    def _on_event(self, path: str) -> None:
        if ignorable(path) or self._excluded(path):
            return
        self.deb.touch(path)

    def start(self) -> None:
        from watchdog.observers import Observer
        self._obs = Observer()
        h = _Handler(self._on_event)
        for r in self.roots:
            if r.is_dir():
                self._obs.schedule(h, str(r), recursive=True)
        self._obs.start()

    def stop(self) -> None:
        if self._obs:
            self._obs.stop()
            self._obs.join(timeout=5)
            self._obs = None

    def flush(self) -> list[Path]:
        return [Path(p) for p in self.deb.due()]


class ChatDbWatcher:
    """Watches a chat database (and its -wal) for changes; 5 s of quiet means 'read the new rows now'."""

    def __init__(self, db_path: Path, source_name: str, *, delay_s: float = 5.0, clock: Clock | None = None):
        self.db_path, self.source = Path(db_path), source_name
        self.deb = Debouncer(delay_s, clock or Clock())
        # not -shm: our own read-only reads write it, which re-triggered discover every ~6 s
        self._names = {self.db_path.name, self.db_path.name + "-wal"}
        self._obs = None

    def _on_event(self, path: str) -> None:
        if Path(path).name in self._names:
            self.deb.touch(self.source)

    def start(self) -> None:
        from watchdog.observers import Observer
        self._obs = Observer()
        self._obs.schedule(_Handler(self._on_event), str(self.db_path.parent), recursive=False)
        self._obs.start()

    def stop(self) -> None:
        if self._obs:
            self._obs.stop()
            self._obs.join(timeout=5)
            self._obs = None

    def flush(self) -> list[str]:
        return self.deb.due()


# ---------------------------------------------------------------- job handler

@handler("discover_paths")
async def discover_paths(job: Job, ctx: Ctx) -> None:
    src = ctx.sources.get("folders")
    if src is None:
        return
    now = ctx.clock.now()
    for raw_path in job.payload.get("paths", []):
        path = Path(raw_path)
        item = src.item_for_path(path) if path.exists() else None
        if item is not None:
            if upsert_item(ctx.con, item, now):
                ctx.queue.enqueue("prep", item_id=item.id)
            continue
        if not path.exists():
            row = ctx.con.execute("SELECT id FROM items WHERE source IN ('folders','repos') AND locator=?",
                                  (str(path),)).fetchone()
            if row:
                ctx.index.remove_item(row["id"])                 # gone from search; its note stays (history kept)
                ctx.con.execute("UPDATE items SET status='skipped', error_code='DELETED', updated_at=? WHERE id=?",
                                (now, row["id"]))
                log_event("PATH_DELETED", item_id=row["id"])
