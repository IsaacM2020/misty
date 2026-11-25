"""The wiki on disk: markdown files in a local git repo. Atomic writes, one commit per batch, never any network."""
from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..clock import Clock, sgt_date
from ..errors import Fatal
from . import notes
from .notes import Page, dump_page, entity_skeleton, links_in, parse_page, slugify, validate_page

DIRS = ("people", "projects", "areas", "topics", "sources", "answers")
SEED = {
    "index.md": "# Index\n",
    "log.md": "# Log\n",
    "corrections.md": "# Corrections\n",
    "about-isaac.md": "---\ntype: about\ntitle: About Isaac\nupdated: \"\"\n---\n\n(empty)\n",
    "priorities.md": "---\ntype: priorities\ntitle: Priorities\nstatus: draft\nupdated: \"\"\n---\n\n## Priorities\n",
}
GIT_ID = ["-c", "user.name=Misty", "-c", "user.email=misty@localhost", "-c", "commit.gpgsign=false"]
TYPE_FOR_FOLDER = {"people": "person", "projects": "project", "topics": "topic", "areas": "area"}


class WikiStore:
    def __init__(self, root: Path, clock: Clock):
        self.root, self.clock = Path(root), clock
        self._depth = 0
        self._batch_msg: str | None = None

    # ---- git
    def _git(self, *args: str, check: bool = True) -> str:
        import time
        for attempt in range(6):                  # another Misty process (CLI synth) may hold index.lock briefly
            r = subprocess.run(["git", "-C", str(self.root), *GIT_ID, *args], capture_output=True, text=True)
            if r.returncode == 0 or "index.lock" not in r.stderr:
                break
            time.sleep(0.2 * (attempt + 1))
        if check and r.returncode != 0:
            raise Fatal("GIT_FAILED")
        return r.stdout

    def _commit(self, message: str) -> None:
        self._git("add", "-A")
        if self._git("status", "--porcelain").strip():
            self._git("commit", "-q", "-m", message)

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    # ---- lifecycle
    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fresh = not (self.root / ".git").exists()
        if fresh:
            subprocess.run(["git", "-C", str(self.root), "init", "-q", "-b", "main"], check=True)
        today = sgt_date(self.clock.now())
        for d in DIRS:
            (self.root / d).mkdir(exist_ok=True)
            keep = self.root / d / ".gitkeep"
            if not keep.exists():
                keep.write_text("")
        for name, content in SEED.items():
            p = self.root / name
            if not p.exists():
                p.write_text(content.replace('updated: ""', f"updated: {today}"))
        if fresh:
            self._commit("init wiki")

    # ---- reading and writing
    def exists(self, rel: str) -> bool:
        return (self.root / rel).is_file()

    def read(self, rel: str) -> Page | None:
        p = self.root / rel
        if not p.is_file():
            return None
        text = p.read_text()
        if not text.startswith("---\n"):
            return Page(rel, {}, text)
        fm, body = parse_page(text)
        return Page(rel, fm, body)

    def write(self, rel: str, fm: dict, body: str) -> None:
        if ".." in Path(rel).parts or rel.startswith("/"):
            raise Fatal("BAD_PATH")
        errs = validate_page(fm, body)
        if errs:
            raise Fatal("INVALID_PAGE:" + ",".join(errs))
        self._write_raw(rel, dump_page(fm, body))

    def _write_raw(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.replace(tmp, p)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        if self._depth == 0:
            self._commit(f"write {rel}")

    def write_text(self, rel: str, text: str) -> None:
        """Free-form files (index.md, log.md, corrections.md)."""
        self._write_raw(rel, text)

    def delete(self, rel: str) -> None:
        (self.root / rel).unlink(missing_ok=True)
        if self._depth == 0:
            self._commit(f"delete {rel}")

    def all_pages(self) -> Iterator[str]:
        for p in sorted(self.root.rglob("*.md")):
            rel = p.relative_to(self.root).as_posix()
            if not rel.startswith("."):
                yield rel

    # ---- batches
    @contextmanager
    def batch(self, message: str):
        """Everything written inside is one commit; log.md gets one line."""
        self._depth += 1
        try:
            yield self
        finally:
            self._depth -= 1
        if self._depth == 0:
            ts = datetime.fromtimestamp(self.clock.now(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            log = self.root / "log.md"
            with open(log, "a") as f:
                f.write(f"- {ts} {message}\n")
            self._commit(message)

    def revert(self, commit: str) -> None:
        self._git("revert", "--no-edit", commit)

    # ---- text for a model prompt (the only door into ApiRunner for wiki content)
    def llm_text(self, rel: str, *, sections=None, max_tokens: int | None = None):
        from ..types import approx_tokens
        from ..runner import WikiText
        page = self.read(rel)
        if page is None:
            return WikiText("")
        parts = [f"### [[{rel[:-3]}]] ({page.fm.get('type', '')} · {page.fm.get('title', '')})"]
        if sections:
            for name in sections:
                body = notes.section(page.body, name)
                if body:
                    parts.append(f"## {name}\n{body}")
        else:
            parts.append(page.body)
        text = "\n".join(parts)
        if max_tokens and approx_tokens(text) > max_tokens:
            text = text[: max_tokens * 4]
        return WikiText(text)

    # ---- stubs and links
    def ensure_stub(self, link: str) -> str:
        rel = link if link.endswith(".md") else link + ".md"
        if self.exists(rel):
            return rel
        folder, _, name = rel[:-3].partition("/")
        kind = TYPE_FOR_FOLDER.get(folder)
        if kind is None or not name:
            raise Fatal("BAD_LINK")
        fm, body = entity_skeleton(kind, name.replace("-", " ").title(), sgt_date(self.clock.now()))
        self.write(rel, fm, body)
        return rel

    def resolve_links(self, body: str) -> list[str]:
        created = []
        for link in links_in(body):
            rel = link + ".md"
            if not self.exists(rel) and link.split("/")[0] in TYPE_FOR_FOLDER:
                created.append(self.ensure_stub(link))
        return created
