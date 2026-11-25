"""claude-mem: Isaac's AI memory database, read-only. Already summarised, so it never goes to an LLM."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Iterator

from ..errors import SchemaUnrecognized
from ..types import RawItem

DEFAULT_DB = Path.home() / ".claude-mem" / "claude-mem.db"

REQUIRED = {
    "observations": {"id", "project", "type", "title", "subtitle", "narrative", "facts", "concepts", "created_at"},
    "session_summaries": {"id", "project", "request", "investigated", "learned", "completed", "next_steps",
                          "notes", "created_at"},
}


def _ts(s: str | None) -> str | None:
    """'2026-10-14T04:28:40.930Z' -> '2026-10-14T04:28:40Z' (the format file items use)."""
    return (s.split(".")[0].rstrip("Z") + "Z") if s else None


class ClaudeMemSource:
    name = "claude_mem"

    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.stats: Counter = Counter()
        self._cursor: dict[str, int] = {"obs": 0, "sum": 0}

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return con

    def check_schema(self) -> None:
        if not self.db_path.exists():
            return
        con = self._connect()
        try:
            for table, cols in REQUIRED.items():
                have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
                if not cols <= have:
                    raise SchemaUnrecognized("CLAUDE_MEM_SCHEMA")
        finally:
            con.close()

    def next_cursor(self) -> str | None:
        return json.dumps(self._cursor)

    def discover(self, cursor: str | None, limit: int | None = None) -> Iterator[RawItem]:
        cur = json.loads(cursor) if cursor else {"obs": 0, "sum": 0}
        self._cursor = {"obs": cur.get("obs", 0), "sum": cur.get("sum", 0)}
        if not self.db_path.exists():
            self.stats["missing"] += 1
            return
        self.check_schema()
        n = 0
        con = self._connect()
        try:
            for r in con.execute(
                    "SELECT id, project, type, title, subtitle, created_at FROM observations WHERE id>? ORDER BY id",
                    (self._cursor["obs"],)):
                if limit is not None and n >= limit:
                    return
                self._cursor["obs"] = r["id"]
                n += 1
                yield RawItem(source=self.name, kind="mem_obs", locator=f"claude_mem:obs:{r['id']}",
                              title=(r["title"] or r["subtitle"] or r["type"] or "observation")[:120],
                              created_at=_ts(r["created_at"]), modified_at=_ts(r["created_at"]),
                              meta={"project": r["project"], "type": r["type"]})
            for r in con.execute(
                    "SELECT id, project, request, created_at FROM session_summaries WHERE id>? ORDER BY id",
                    (self._cursor["sum"],)):
                if limit is not None and n >= limit:
                    return
                self._cursor["sum"] = r["id"]
                n += 1
                yield RawItem(source=self.name, kind="mem_summary", locator=f"claude_mem:sum:{r['id']}",
                              title=((r["request"] or "session summary").strip().splitlines() or ["session summary"])[0][:80],
                              created_at=_ts(r["created_at"]), modified_at=_ts(r["created_at"]),
                              meta={"project": r["project"]})
        finally:
            con.close()

    def load_text(self, item: RawItem) -> str:
        _, kind, ident = item.locator.split(":")
        con = self._connect()
        try:
            if kind == "obs":
                r = con.execute("SELECT title, subtitle, narrative, facts, concepts FROM observations WHERE id=?",
                                (int(ident),)).fetchone()
                parts = [("Title", r["title"]), ("Subtitle", r["subtitle"]), ("Narrative", r["narrative"]),
                         ("Facts", r["facts"]), ("Concepts", r["concepts"])] if r else []
            else:
                r = con.execute("SELECT request, investigated, learned, completed, next_steps, notes "
                                "FROM session_summaries WHERE id=?", (int(ident),)).fetchone()
                parts = [("Request", r["request"]), ("Investigated", r["investigated"]), ("Learned", r["learned"]),
                         ("Completed", r["completed"]), ("Next steps", r["next_steps"]),
                         ("Notes", r["notes"])] if r else []
        finally:
            con.close()
        if not parts:
            raise FileNotFoundError(item.locator)
        return "\n".join(f"{k}: {v}" for k, v in parts if v)
