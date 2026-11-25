"""The "Known names" block of the compile prompt (spec 3.3).

The cheapest place to prevent a duplicate is the compiler: if it writes the spelling the wiki already uses, the
resolver's exact match settles it with no judge. Selection is deterministic and local (no network): names whose words
appear in the item, the chat's counterpart and recent entities, and the nearest topics by vector. The block goes in
the user message, so the cached SYSTEM prefix is never touched."""
from __future__ import annotations

import json
import re
from collections import defaultdict

import numpy as np

from ..scrub import find_secrets
from .names import HONORIFICS, norm
from .store import Registry

PER_ITEM = 15
PER_BATCH = 40
RECENT_IN_CHAT = 10
NEAREST_TOPICS = 5
_HEADER = ("Known names (use the exact spelling when the item means the same one, otherwise write the name as "
           "the text gives it):")
_GROUPS = (("person", "People"), ("project", "Projects"), ("topic", "Topics"))


def _tokens(text: str) -> set[str]:
    return {t for t in norm(text).split() if len(t) >= 3 and t not in HONORIFICS}


def _clean_name(name: str) -> str | None:
    n = " ".join(name.split())
    if not n or "[REDACTED" in n or find_secrets(n, "file") or ";" in n:
        return None
    return n


def known_block(ctx, rows, *, per_item: int = PER_ITEM, per_batch: int = PER_BATCH, texts: dict | None = None) -> str:
    """Up to `per_batch` entity names for this batch of items, people first. "" when the registry is empty."""
    reg = Registry(ctx.con, ctx.clock)
    ents = {e["id"]: e for e in reg.entities()}
    if not ents:
        return ""
    from ..cloud import item_text
    from . import privacy
    from .sweep import public_source_count

    # inverted index: alias token -> [(entity id, alias token set)]
    index: dict[str, list[tuple[str, frozenset[str]]]] = defaultdict(list)
    for r in ctx.con.execute("SELECT entity_id, alias_norm FROM entity_aliases"):
        if r["entity_id"] not in ents:
            continue
        toks = frozenset(_tokens(r["alias_norm"]))
        if toks:
            for t in toks:
                index[t].append((r["entity_id"], toks))
    mentions = {r["entity_id"]: r["n"] for r in ctx.con.execute(
        "SELECT entity_id, COUNT(DISTINCT item_id) n FROM mentions GROUP BY entity_id")}
    keys = privacy.private_keys(ctx.settings)
    ok_cache: dict[str, bool] = {}

    def usable(eid: str) -> bool:
        if eid not in ok_cache:
            e = ents[eid]
            ok_cache[eid] = bool(e["known"] or e["origin"] in ("contact", "user") or public_source_count(ctx, eid) > 0)
        return ok_cache[eid]

    topic_vecs = None
    chosen: dict[str, None] = {}
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        text = (texts or {}).get(r["id"])
        if text is None:
            text = item_text(ctx, r["id"])
        item_pick: list[str] = []
        # 1. names whose words all appear in the item
        words = _tokens(text)
        hit: dict[str, float] = {}
        for t in words:
            for eid, toks in index.get(t, ()):
                if toks <= words:
                    hit[eid] = max(hit.get(eid, 0.0), len(toks))
        item_pick += [e for e in sorted(hit, key=lambda e: (-hit[e], -mentions.get(e, 0), ents[e]["name"].lower()))
                      if usable(e)]
        # 2. the chat's counterpart and the people last seen in the same chat
        cp = meta.get("counterpart")
        cp_handle = cp.get("handle") if isinstance(cp, dict) else None
        if isinstance(cp_handle, list) and len(cp_handle) == 2:
            e = reg.by_handle(cp_handle[0], cp_handle[1])
            if e in ents and e not in item_pick:
                item_pick.append(e)
        cid = meta.get("chat_id")
        if cid and f"{r['source']}:{cid}" not in keys:
            for m in ctx.con.execute(
                    "SELECT m.entity_id FROM mentions m JOIN items i ON i.id = m.item_id "
                    "WHERE json_extract(i.meta,'$.chat_id')=? AND i.source=? AND i.id != ? "
                    "GROUP BY m.entity_id ORDER BY MAX(i.modified_at) DESC, m.entity_id LIMIT ?",
                    (cid, r["source"], r["id"], RECENT_IN_CHAT)):
                s = reg.survivor(m["entity_id"])
                if s in ents and s not in item_pick and usable(s):
                    item_pick.append(s)
        # 3. the nearest topics by name vector
        if ctx.embedder is not None and text.strip():
            if topic_vecs is None:
                topic_vecs = {e: np.frombuffer(v, dtype=np.float32) for e, v in ctx.con.execute(
                    "SELECT v.entity_id, v.vec FROM entity_vec v JOIN entities e ON e.id = v.entity_id "
                    "WHERE e.kind='topic' AND e.status IN ('candidate','active')")}
            if topic_vecs:
                q = np.asarray(ctx.embedder.embed_documents([text[:600]])[0], dtype=np.float32)
                ranked = sorted(((float(np.dot(q, v)), e) for e, v in topic_vecs.items() if e in ents),
                                key=lambda t: (-t[0], t[1]))
                item_pick += [e for _, e in ranked[:NEAREST_TOPICS] if e not in item_pick and usable(e)]
        for e in item_pick[:per_item]:
            chosen.setdefault(e, None)

    lines: list[str] = []
    budget = per_batch
    for kind, label in _GROUPS:
        names: list[str] = []
        for eid in chosen:
            if ents[eid]["kind"] != kind or budget <= 0:
                continue
            n = _clean_name(ents[eid]["name"])
            if n and n not in names:
                names.append(n)
                budget -= 1
        if names:
            lines.append(f"{label}: " + "; ".join(names))
    return f"{_HEADER}\n" + "\n".join(lines) if lines else ""
