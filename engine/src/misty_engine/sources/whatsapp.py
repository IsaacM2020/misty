"""WhatsApp Desktop local database, read-only. Only chats Isaac picks. Needs Full Disk Access (M6 for real data)."""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..clock import SGT
from ..errors import SchemaUnrecognized
from ..types import RawItem
from .base import ChatInfo, parse_scope_cursor, scope_cursor
from .chatmeta import counterpart, phone_handle
from .typedstream import APPLE_EPOCH

WA_DB = (Path.home() / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared"
         / "ChatStorage.sqlite")

REQUIRED = {
    "ZWACHATSESSION": {"Z_PK", "ZCONTACTJID", "ZPARTNERNAME", "ZLASTMESSAGEDATE"},
    "ZWAMESSAGE": {"Z_PK", "ZCHATSESSION", "ZISFROMME", "ZTEXT", "ZMESSAGEDATE"},
}


def _unix(v) -> float | None:
    """WhatsApp dates are seconds since 2001. Real databases hold NULL, 0 and absurd values; those are not dates."""
    try:
        u = float(v) + APPLE_EPOCH
    except (TypeError, ValueError):
        return None
    return u if v and 978307200 < u < 4102444800 else None          # 2001 .. 2100


def _ts(v) -> str | None:
    u = _unix(v)
    return datetime.fromtimestamp(u, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if u else None


def _iso_week(unix: float) -> str:
    y, w, _ = datetime.fromtimestamp(unix, SGT).isocalendar()
    return f"{y}-W{w:02d}"


class WhatsAppSource:
    name = "whatsapp"

    def __init__(self, db_path: Path = WA_DB, picked: set[str] | None = None):
        self.db_path = Path(db_path)
        self.picked = set(picked or ())
        self.stats: Counter = Counter()
        self._cursor: int | None = None

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return con

    def check_access(self) -> bool:
        try:
            con = self._connect()
            try:
                con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
            finally:
                con.close()
            return True
        except (sqlite3.OperationalError, PermissionError, OSError):
            return False

    def check_schema(self) -> None:
        con = self._connect()
        try:
            for table, cols in REQUIRED.items():
                have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
                if not cols <= have:
                    raise SchemaUnrecognized("WHATSAPP_SCHEMA")
        finally:
            con.close()

    def next_cursor(self) -> str | None:
        return None if self._cursor is None else scope_cursor(self.picked, self._cursor)

    def _has_group_members(self, con) -> bool:
        have = {r["name"] for r in con.execute("PRAGMA table_info(ZWAMESSAGE)")}
        tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return "ZGROUPMEMBER" in have and "ZWAGROUPMEMBER" in tables

    def _select(self, con) -> str:
        member = self._has_group_members(con)
        return (
            "SELECT m.Z_PK AS pk, m.ZTEXT AS text, m.ZMESSAGEDATE AS date, m.ZISFROMME AS me, "
            "s.ZCONTACTJID AS jid, s.ZPARTNERNAME AS partner, "
            + ("g.ZCONTACTNAME AS member " if member else "NULL AS member ")
            + "FROM ZWAMESSAGE m JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION "
            + ("LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER " if member else ""))

    def _sender(self, r) -> str:
        if r["me"]:
            return "Isaac"
        return r["member"] or r["partner"] or "unknown"

    def _lines(self, rows) -> str:
        out = []
        for r in rows:
            t = (r["text"] or "").strip()
            u = _unix(r["date"])
            if not t or u is None:
                continue
            when = datetime.fromtimestamp(u, SGT).strftime("%Y-%m-%d %H:%M")
            out.append(f"{when} {self._sender(r)}: {t}")
        return "\n".join(out)

    def discover(self, cursor: str | None, limit: int | None = None) -> Iterator[RawItem]:
        after = parse_scope_cursor(cursor, self.picked)
        self._cursor = after
        if not self.picked:
            return
        con = self._connect()
        try:
            rows = con.execute(self._select(con) + "WHERE m.Z_PK > ? ORDER BY m.Z_PK", (after,)).fetchall()
        finally:
            con.close()
        groups: dict[tuple[str, str], dict] = defaultdict(lambda: {"last": 0})
        for r in rows:
            self._cursor = max(self._cursor, r["pk"])
            u = _unix(r["date"])
            if r["jid"] not in self.picked or not (r["text"] or "").strip() or u is None:
                continue
            g = groups[(r["jid"], _iso_week(u))]
            g["last"], g["name"], g["partner"] = max(g["last"], r["pk"]), r["partner"] or r["jid"], r["partner"]
        n = 0
        for (jid, week), g in sorted(groups.items()):
            if limit is not None and n >= limit:
                return
            n += 1
            meta = {"chat_id": jid, "week": week, "last_rowid": g["last"], "rev": g["last"],
                    "chat_kind": "group" if jid.endswith("@g.us") else "1to1"}
            if meta["chat_kind"] == "1to1":
                meta["counterpart"] = counterpart(g.get("partner"), phone_handle(jid) or ["jid", jid], "chat")
            yield RawItem(source=self.name, kind="chat_week", locator=f"whatsapp:{jid}:{week}",
                          title=f"{g['name']} · {week}", meta=meta)

    def load_text(self, item: RawItem) -> str:
        jid, week = item.meta["chat_id"], item.meta["week"]
        con = self._connect()
        try:
            rows = con.execute(self._select(con) + "WHERE s.ZCONTACTJID = ? ORDER BY m.ZMESSAGEDATE, m.Z_PK",
                               (jid,)).fetchall()
        finally:
            con.close()
        return self._lines(r for r in rows if _unix(r["date"]) is not None and _iso_week(_unix(r["date"])) == week)

    def new_messages_text(self, item: RawItem, after_rowid: int) -> str:
        con = self._connect()
        try:
            rows = con.execute(self._select(con) + "WHERE s.ZCONTACTJID = ? AND m.Z_PK > ? "
                               "ORDER BY m.ZMESSAGEDATE, m.Z_PK", (item.meta["chat_id"], after_rowid)).fetchall()
        finally:
            con.close()
        return self._lines(rows)

    def list_chats(self) -> list[ChatInfo]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT s.ZCONTACTJID AS jid, s.ZPARTNERNAME AS name, s.ZLASTMESSAGEDATE AS last, "
                "COUNT(m.Z_PK) AS n FROM ZWACHATSESSION s LEFT JOIN ZWAMESSAGE m ON m.ZCHATSESSION = s.Z_PK "
                "GROUP BY s.Z_PK ORDER BY last DESC").fetchall()
        finally:
            con.close()
        return [ChatInfo(r["jid"], r["name"] or r["jid"], _ts(r["last"]), r["n"])
                for r in rows]
