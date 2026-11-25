"""Which items are kept out of context packs (spec 3.10). Pure lookups, no model calls."""
from __future__ import annotations

import json


def private_keys(settings) -> frozenset[str]:
    return frozenset(settings.get("context_private_chats") or [])


def item_is_private(source: str, meta: dict, keys: frozenset[str]) -> bool:
    cid = meta.get("chat_id")
    return bool(cid) and f"{source}:{cid}" in keys


def is_private_item(con, settings, item_id: str) -> bool:
    r = con.execute("SELECT source, meta FROM items WHERE id=?", (item_id,)).fetchone()
    return bool(r) and item_is_private(r["source"], json.loads(r["meta"] or "{}"), private_keys(settings))
