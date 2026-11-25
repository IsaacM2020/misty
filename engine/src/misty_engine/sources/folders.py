"""Filesystem walker. Never follows symlinks, never downloads iCloud placeholders, never opens secret files."""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..extract import DOC_EXT, IMAGE_EXT, MAX_BYTES, TEXT_EXT, extract_text
from ..scrub import is_secret_file
from ..types import RawItem
from .repos import SKIP_DIRS, repo_summary_text

SF_DATALESS = 0x40000000
SUPPORTED = TEXT_EXT | DOC_EXT | IMAGE_EXT


def file_flags(path: Path) -> int:
    return getattr(os.stat(path, follow_symlinks=False), "st_flags", 0)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FolderSource:
    name = "folders"
    item_sources = ("folders", "repos")      # source names this object can load_text() for

    def __init__(self, roots: list[Path], excluded: list[Path], include_images: bool = False):
        self.include_images = include_images
        self.roots = [Path(r) for r in roots]
        self.excluded = [Path(os.path.realpath(e)) for e in excluded]
        self._root_set = {Path(os.path.realpath(r)) for r in self.roots}
        self.stats: Counter = Counter()

    def next_cursor(self) -> str | None:
        return None      # the walk is cheap; unchanged files are skipped by mtime/size in upsert_item

    # -- walking
    def _is_excluded(self, real: Path) -> bool:
        return any(real == e or e in real.parents for e in self.excluded)

    def discover(self, cursor: str | None = None, limit: int | None = None) -> Iterator[RawItem]:
        n = 0
        for root in self.roots:
            real_root = Path(os.path.realpath(root))
            if not real_root.is_dir() or self._is_excluded(real_root):
                continue
            for item in self._walk(real_root, real_root):
                yield item
                n += 1
                if limit is not None and n >= limit:
                    return

    def _walk(self, d: Path, root: Path) -> Iterator[RawItem]:
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            self.stats["no_access"] += 1
            return
        for e in entries:
            path = Path(e.path)
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    yield from self._dir(path, root)
                elif e.is_file(follow_symlinks=False):
                    item = self._file(path)
                    if item is not None:
                        yield item
            except OSError:
                self.stats["no_access"] += 1

    def _dir(self, path: Path, root: Path) -> Iterator[RawItem]:
        name = path.name
        if name in SKIP_DIRS or name.startswith("."):
            return
        if self._is_excluded(path):
            return
        if path in self._root_set and path != root:
            return      # another configured root: walked there, never twice
        if (path / ".git").exists():
            st = path.stat()
            readme_m = max((p.stat().st_mtime for p in path.glob("README*") if p.is_file()), default=0)
            m = max(st.st_mtime, readme_m)
            yield RawItem(source="repos", kind="repo", locator=str(path), title=name,
                          created_at=_iso(getattr(st, "st_birthtime", st.st_ctime)), modified_at=_iso(m),
                          meta={"size": 0, "ext": "", "mtime": m})
            return
        yield from self._walk(path, root)

    def _file(self, path: Path) -> RawItem | None:
        name = path.name
        if name.endswith(".icloud"):
            self.stats["dataless"] += 1
            return None
        secret = is_secret_file(name)
        ext = path.suffix.lower()
        if not secret and ext not in SUPPORTED:
            return None
        if not secret and ext in IMAGE_EXT and not self.include_images:
            self.stats["images_deferred"] += 1
            return None
        st = os.stat(path, follow_symlinks=False)
        if not secret:
            if file_flags(path) & SF_DATALESS:
                self.stats["dataless"] += 1
                return None
            if st.st_size > MAX_BYTES:
                self.stats["too_big"] += 1
                return None
        kind = "secret_file" if secret else ("image" if ext in IMAGE_EXT else "file")
        return RawItem(source="folders", kind=kind, locator=str(path), title=name,
                       created_at=_iso(getattr(st, "st_birthtime", st.st_ctime)), modified_at=_iso(st.st_mtime),
                       meta={"size": st.st_size, "ext": ext, "mtime": st.st_mtime})

    # -- single path (used by the file watcher)
    def item_for_path(self, path: Path) -> RawItem | None:
        """The item a changed path would be, or None when the walker would have skipped it."""
        path = Path(path)
        real = Path(os.path.realpath(path.parent)) / path.name
        if not any(real == r or r in real.parents for r in self._root_set):
            return None
        root = next(r for r in self._root_set if r == real or r in real.parents)
        for part in real.relative_to(root).parts[:-1]:
            if part in SKIP_DIRS or part.startswith("."):
                return None
        if self._is_excluded(real) or real.is_symlink() or not real.is_file():
            return None
        try:
            return self._file(real)
        except OSError:
            return None

    # -- reading
    def load_text(self, item: RawItem) -> str:
        if item.kind == "secret_file":
            return Path(item.locator).name         # never opened
        if item.kind == "repo":
            return repo_summary_text(Path(item.locator))
        if item.kind == "image":
            return ""
        return extract_text(Path(item.locator)).text
