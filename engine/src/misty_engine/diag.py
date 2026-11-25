"""Counts-only health checks for the chat sources. Never returns, logs or stores message text or names.

Exists because the engine child is the only process with Full Disk Access: a human or a test harness
cannot look at the real chat.db, but can ask the engine for these numbers over the socket.
"""
from __future__ import annotations

import re
from collections import Counter
import os
from pathlib import Path

from .errors import SchemaUnrecognized
from .extract import IMAGE_EXT, MAX_BYTES
from .sources.contacts import ContactsResolver
from .sources.imessage import DEFAULT_DB, IMessageSource
from .sources.whatsapp import REQUIRED as WA_REQUIRED, WA_DB, WhatsAppSource
from .sources.typedstream import decode_attributed_body

_PHONE = re.compile(r"\+?\d[\d ()-]{5,}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
_OBJECT_CHAR = "￼"          # attachment placeholder inside text
_REPLACEMENT = "�"          # utf-8 decode error marker, means the length slice was wrong


def title_kind(name: str) -> str:
    n = (name or "").strip()
    if n == "Group chat":
        return "unnamed_group"
    if _PHONE.fullmatch(n):
        return "raw_phone"
    if _EMAIL.fullmatch(n):
        return "raw_email"
    return "named"


def _shape(blob: bytes) -> str:
    """Structure of a typedstream blob around the string length, never the text itself."""
    i = blob.find(b"NSString")
    if i < 0:
        return "no_marker"
    j = i + len(b"NSString") + 5
    lenbyte = f"{blob[j]:#04x}" if j < len(blob) else "past_end"
    size = "lt64" if len(blob) < 64 else "lt256" if len(blob) < 256 else "lt1024" if len(blob) < 1024 else "ge1024"
    return f"marker={blob[i + 8:j].hex()} lenbyte={lenbyte} blob={size}"


def contacts_stats(contacts: ContactsResolver | None) -> dict:
    if contacts is None:
        return {"loaded": False}
    return {"loaded": True, "dbs_found": contacts.dbs_found, "dbs_failed": contacts.dbs_failed,
            "phones": len(contacts._phone), "emails": len(contacts._email)}


def imessage_stats(db_path: Path = DEFAULT_DB, contacts: ContactsResolver | None = None) -> dict:
    src = IMessageSource(db_path, contacts=contacts)
    con = src._connect()
    try:
        have = {r["name"] for r in con.execute("PRAGMA table_info(message)")}
        extra = [f"m.{k} AS {k}" for k in ("associated_message_type", "item_type", "balloon_bundle_id") if k in have]
        rows = con.execute(f"SELECT m.text, m.attributedBody{''.join(', ' + e for e in extra)} FROM message m "
                           "JOIN chat_message_join j ON j.message_id = m.ROWID").fetchall()
    finally:
        con.close()
    c: Counter = Counter()
    odd: Counter = Counter()

    def _odd_key(r, blob) -> str:
        attrs = {k: r[k] for k in ("associated_message_type", "item_type") if k in r.keys()}
        balloon = bool(r["balloon_bundle_id"]) if "balloon_bundle_id" in r.keys() else None
        return f"{_shape(blob)} balloon={balloon} {attrs}"

    for r in rows:
        c["rows"] += 1
        text, blob = (r["text"] or "").strip(), r["attributedBody"]
        if text:
            c["text_column"] += 1
            continue
        if not blob:
            c["no_body"] += 1                      # attachment-only, read receipts and similar
            continue
        decoded = decode_attributed_body(blob)
        if decoded is None:
            c["blob_no_marker"] += 1
        elif not decoded.strip():
            c["blob_empty_text"] += 1
            odd[_odd_key(r, blob)] += 1
        else:
            c["blob_decoded"] += 1
            if _REPLACEMENT in decoded:
                c["blob_replacement_chars"] += 1
                odd["replacement:" + _odd_key(r, blob)] += 1
            if decoded.strip(_OBJECT_CHAR + " \n\t") == "":
                c["blob_object_char_only"] += 1
    with_body = c["text_column"] + c["blob_decoded"] + c["blob_no_marker"] + c["blob_empty_text"]
    usable = c["text_column"] + c["blob_decoded"] - c["blob_object_char_only"]
    chats = src.list_chats()
    kinds = Counter(title_kind(ch.name) for ch in chats)
    return {
        "rows": c["rows"], "with_body": with_body, "usable_text": usable,
        "usable_pct": round(100 * usable / with_body, 1) if with_body else None,
        "breakdown": dict(c), "odd_rows": dict(odd),
        "chats": len(chats), "chat_titles": dict(kinds),
        "contacts": contacts_stats(contacts),
    }


def whatsapp_stats(db_path: Path = WA_DB) -> dict:
    """Schema verdict (table and column names only) plus counts; no jids, names or text."""
    src = WhatsAppSource(Path(db_path))
    out: dict = {"exists": Path(db_path).exists()}
    out["access"] = out["exists"] and src.check_access()
    if not out["access"]:
        return out
    con = src._connect()
    try:
        try:
            src.check_schema()
            out["schema"] = "ok"
        except SchemaUnrecognized:
            out["schema"] = "unrecognized"
            tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            out["tables_zwa"] = sorted(t for t in tables if t.startswith("ZWA"))
            missing = {}
            for table, cols in WA_REQUIRED.items():
                have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
                if not cols <= have:
                    missing[table] = {"missing": sorted(cols - have), "table_present": table in tables}
            out["missing"] = missing
            return out
        one = lambda sql: con.execute(sql).fetchone()[0]          # noqa: E731
        out.update({
            "chats": one("SELECT COUNT(*) FROM ZWACHATSESSION"),
            "groups": one("SELECT COUNT(*) FROM ZWACHATSESSION WHERE ZCONTACTJID LIKE '%@g.us'"),
            "chats_with_name": one("SELECT COUNT(*) FROM ZWACHATSESSION WHERE COALESCE(ZPARTNERNAME,'') <> ''"),
            "messages": one("SELECT COUNT(*) FROM ZWAMESSAGE"),
            "messages_with_text": one("SELECT COUNT(*) FROM ZWAMESSAGE WHERE COALESCE(TRIM(ZTEXT),'') <> ''"),
            "group_member_names": src._has_group_members(con),
        })
        out["text_pct"] = round(100 * out["messages_with_text"] / out["messages"], 1) if out["messages"] else None
    finally:
        con.close()
    return out


PHOTOS_DEFAULT = Path.home() / "Pictures" / "Photos Library.photoslibrary"
IMAGES_PER_SEC = 1.7          # M0 benchmark (docs/verify/embedding.md), one image at a time at 448x448


def photos_stats(library: Path = PHOTOS_DEFAULT) -> dict:
    """What a Photos index would cost, from `originals/` only. Counts by extension, never names or paths."""
    originals = Path(library) / "originals"
    out: dict = {"exists": originals.is_dir()}
    if not out["exists"]:
        return out
    ext: Counter = Counter()
    size = over_cap = other = 0
    try:
        for root, _dirs, files in os.walk(originals):
            for f in files:
                e = os.path.splitext(f)[1].lower()
                if e not in IMAGE_EXT:
                    other += 1
                    continue
                ext[e] += 1
                sz = os.path.getsize(os.path.join(root, f))
                size += sz
                over_cap += sz > MAX_BYTES
    except PermissionError:
        out["access"] = False
        return out
    n = sum(ext.values())
    out.update({"access": True, "images": n, "by_ext": dict(ext), "other_files": other,
                "image_gb": round(size / 1e9, 2), "over_size_cap": over_cap,
                "est_minutes": round(n / IMAGES_PER_SEC / 60, 1)})
    return out
