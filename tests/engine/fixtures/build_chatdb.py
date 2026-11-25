"""Builds a fake Messages chat.db and a fake AddressBook for tests. No real data."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

APPLE_EPOCH = 978307200


def apple_ns(iso: str) -> int:
    dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    return int((dt.timestamp() - APPLE_EPOCH) * 1e9)


def typedstream_blob(s: str) -> bytes:
    raw = s.encode()
    n = len(raw)
    if n < 0x80:
        ln = bytes([n])
    elif n < 0x10000:
        ln = b"\x81" + n.to_bytes(2, "little")
    else:
        ln = b"\x82" + n.to_bytes(4, "little")
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + ln + raw + b"\x86\x84\x02iI\x01"


LONG = "long message " * 24     # > 300 chars, forces the 0x81 length form

# (rowid, chat_rowid, iso time, is_from_me, handle_rowid, text, use_attributed_body)
MESSAGES = [
    (1, 1, "2026-10-05T10:00:00", 0, 1, "hey are we meeting for nets", False),
    (2, 1, "2026-10-05T10:01:00", 1, 0, "yes saturday 7am", False),
    (3, 1, "2026-10-12T09:00:00", 0, 1, "iGEM wiki deadline is friday", True),
    (4, 1, "2026-10-13T09:00:00", 1, 0, LONG, True),
    (5, 2, "2026-10-06T20:00:00", 0, 2, "dinner at 8?", False),
    (6, 2, "2026-10-13T20:00:00", 1, 0, "kya scene hai 🏏", False),
    (7, 2, "2026-10-13T20:05:00", 0, 2, None, False),     # empty (e.g. an attachment), must be skipped
]


def build_chatdb(path: Path, *, drop_attributed_body: bool = False) -> Path:
    c = sqlite3.connect(path)
    cols = "ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, date INTEGER, is_from_me INTEGER, handle_id INTEGER"
    if not drop_attributed_body:
        cols += ", attributedBody BLOB"
    c.execute(f"CREATE TABLE message ({cols})")
    c.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT)")
    c.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT)")
    c.execute("CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER)")
    c.execute("INSERT INTO handle VALUES (1, '+6591234567'), (2, 'friend@example.com')")
    c.execute("INSERT INTO chat VALUES (1, 'iMessage;-;+6591234567', '+6591234567', ''), "
              "(2, 'iMessage;-;friend@example.com', 'friend@example.com', 'The Friend')")
    for rowid, chat, iso, me, handle, text, use_blob in MESSAGES:
        body_text = None if use_blob else text
        if drop_attributed_body:
            c.execute("INSERT INTO message(ROWID, guid, text, date, is_from_me, handle_id) VALUES (?,?,?,?,?,?)",
                      (rowid, f"g{rowid}", text, apple_ns(iso), me, handle))
        else:
            c.execute("INSERT INTO message(ROWID, guid, text, date, is_from_me, handle_id, attributedBody) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (rowid, f"g{rowid}", body_text, apple_ns(iso), me, handle,
                       typedstream_blob(text) if use_blob and text else None))
        c.execute("INSERT INTO chat_message_join VALUES (?,?)", (chat, rowid))
    c.commit()
    c.close()
    return path


def build_addressbook(ab_dir: Path) -> Path:
    src = ab_dir / "Sources" / "ABC-123"
    src.mkdir(parents=True)
    p = src / "AddressBook-v22.abcddb"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT)")
    c.execute("CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT)")
    c.execute("CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT)")
    c.execute("INSERT INTO ZABCDRECORD VALUES (1, 'Arjun', 'Rao', NULL), (2, NULL, NULL, 'NUS Cricket Club')")
    c.execute("INSERT INTO ZABCDPHONENUMBER VALUES (1, '+65 9123 4567')")
    c.execute("INSERT INTO ZABCDEMAILADDRESS VALUES (2, 'Club@Example.com')")
    c.commit()
    c.close()
    return p
