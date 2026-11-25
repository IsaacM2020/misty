"""iMessage reader: ~/Library/Messages/chat.db, read-only. Needs Full Disk Access (M6 for real data)."""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterator

from ..clock import SGT
from ..errors import SchemaUnrecognized
from ..types import RawItem
from .base import ChatInfo, parse_scope_cursor, scope_cursor
from .chatmeta import counterpart, email_handle, phone_handle
from .contacts import ContactsResolver
from .typedstream import apple_ts, apple_unix, decode_attributed_body

DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"

REQUIRED = {
    "message": {"ROWID", "guid", "text", "attributedBody", "date", "is_from_me", "handle_id"},
    "handle": {"ROWID", "id"},
    "chat": {"ROWID", "guid", "chat_identifier", "display_name"},
    "chat_message_join": {"chat_id", "message_id"},
}


def iso_week(unix: float) -> str:
    y, w, _ = datetime.fromtimestamp(unix, SGT).isocalendar()
    return f"{y}-W{w:02d}"


class IMessageSource:
    name = "imessage"

    def __init__(self, db_path: Path = DEFAULT_DB, contacts: ContactsResolver | None = None,
                 excluded: set[str] | None = None):
        self.db_path = Path(db_path)
        self.contacts = contacts
        self.excluded = set(excluded or ())
        self.stats: Counter = Counter()
        self._cursor: int | None = None

    # -- access
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return con

    def check_access(self) -> bool:
        try:
            con = self._connect()
            try:
                con.execute("SELECT 1 FROM message LIMIT 1").fetchall()
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
                    raise SchemaUnrecognized("IMESSAGE_SCHEMA")
        finally:
            con.close()

    def next_cursor(self) -> str | None:
        return None if self._cursor is None else scope_cursor(self.excluded, self._cursor)

    # -- helpers
    def _chat_name(self, row) -> str:
        if row["display_name"]:
            return row["display_name"]
        guid = row["chat_guid"] if "chat_guid" in row.keys() else row["guid"]
        if ";+;" in (guid or ""):                            # unnamed group chat: its identifier is a hash
            return "Group chat"
        h = row["chat_identifier"] or guid
        return (self.contacts.name_for(h) if self.contacts else None) or h

    def _sender(self, row) -> str:
        if row["is_from_me"]:
            return "Isaac"
        h = row["handle"] or "unknown"
        return (self.contacts.name_for(h) if self.contacts else None) or h

    def _text(self, row) -> str:
        t = row["text"] or decode_attributed_body(row["attributedBody"]) or ""
        return t.replace("\ufffc", "").strip()          # U+FFFC marks an inline attachment, not words

    def _lines(self, rows) -> str:
        out = []
        for r in rows:
            t = self._text(r)
            if not t:
                continue
            when = datetime.fromtimestamp(apple_unix(r["date"]), SGT).strftime("%Y-%m-%d %H:%M")
            out.append(f"{when} {self._sender(r)}: {t}")
        return "\n".join(out)

    _MSG_SQL = ("SELECT m.ROWID AS rowid, m.text, m.attributedBody, m.date, m.is_from_me, h.id AS handle, "
                "c.guid AS chat_guid, c.chat_identifier, c.display_name "
                "FROM message m JOIN chat_message_join j ON j.message_id = m.ROWID "
                "JOIN chat c ON c.ROWID = j.chat_id LEFT JOIN handle h ON h.ROWID = m.handle_id ")

    # -- Source protocol
    def discover(self, cursor: str | None, limit: int | None = None) -> Iterator[RawItem]:
        after = parse_scope_cursor(cursor, self.excluded)
        self._cursor = after
        con = self._connect()
        try:
            rows = con.execute(self._MSG_SQL + "WHERE m.ROWID > ? ORDER BY m.ROWID", (after,)).fetchall()
        finally:
            con.close()
        groups: dict[tuple[str, str], dict] = defaultdict(lambda: {"last": 0, "n": 0})
        for r in rows:
            self._cursor = max(self._cursor, r["rowid"])
            if r["chat_guid"] in self.excluded or not self._text(r):
                self.stats["skipped_messages"] += 1
                continue
            g = groups[(r["chat_guid"], iso_week(apple_unix(r["date"])))]
            g["last"], g["n"], g["name"] = max(g["last"], r["rowid"]), g["n"] + 1, self._chat_name(r)
            g["ident"], g["display"] = r["chat_identifier"] or "", r["display_name"] or ""
        n = 0
        for (chat, week), g in sorted(groups.items()):
            if limit is not None and n >= limit:
                return
            n += 1
            meta = {"chat_id": chat, "week": week, "last_rowid": g["last"], "rev": g["last"]}
            group = ";+;" in chat or g["ident"].startswith("chat")
            meta["chat_kind"] = "group" if group else "1to1"
            if not group:
                ident = g["ident"] or chat.rsplit(";", 1)[-1]
                cname = self.contacts.name_for(ident) if self.contacts else None
                handle = email_handle(ident) or phone_handle(ident) or ["phone", ident]
                meta["counterpart"] = counterpart(cname or g["display"] or None, handle,
                                                  "contacts" if cname else "chat")
            yield RawItem(source=self.name, kind="chat_week", locator=f"imessage:{chat}:{week}",
                          title=f"{g['name']} · {week}", meta=meta)

    def load_text(self, item: RawItem) -> str:
        chat, week = item.meta["chat_id"], item.meta["week"]
        con = self._connect()
        try:
            rows = con.execute(self._MSG_SQL + "WHERE c.guid = ? ORDER BY m.date, m.ROWID", (chat,)).fetchall()
        finally:
            con.close()
        return self._lines(r for r in rows if iso_week(apple_unix(r["date"])) == week)

    def new_messages_text(self, item: RawItem, after_rowid: int) -> str:
        con = self._connect()
        try:
            rows = con.execute(self._MSG_SQL + "WHERE c.guid = ? AND m.ROWID > ? ORDER BY m.date, m.ROWID",
                               (item.meta["chat_id"], after_rowid)).fetchall()
        finally:
            con.close()
        return self._lines(rows)

    def chat_names(self) -> dict[str, str]:
        """chat guid -> current display name (Contacts names included), used to refresh stored item titles."""
        return {c.chat_id: c.name for c in self.list_chats()}

    def list_chats(self) -> list[ChatInfo]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT c.guid, c.chat_identifier, c.display_name, COUNT(m.ROWID) AS n, MAX(m.date) AS last "
                "FROM chat c JOIN chat_message_join j ON j.chat_id = c.ROWID "
                "JOIN message m ON m.ROWID = j.message_id GROUP BY c.ROWID ORDER BY last DESC").fetchall()
        finally:
            con.close()
        return [ChatInfo(r["guid"], self._chat_name(r), apple_ts(r["last"]) if r["last"] else None, r["n"])
                for r in rows]
