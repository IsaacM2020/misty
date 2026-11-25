"""Hybrid search: vector KNN + FTS5 keyword, merged with reciprocal rank fusion."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from .clock import sgt_day_start
from .ctx import Ctx

DAY = 86400.0

SOURCE_FILTERS: dict[str, str] = {
    "files": "source IN ('folders','repos') AND kind != 'image'",
    "photos": "kind = 'image'",
    "mail": "source = 'gmail'",
    "calendar": "source = 'gcal'",
    "imessage": "source = 'imessage'",
    "whatsapp": "source IN ('whatsapp','whatsapp_export')",
    "claude_mem": "source = 'claude_mem'",
}


@dataclass(frozen=True)
class Hit:
    item_id: str
    title: str
    locator: str
    source: str
    kind: str
    snippet: str
    score: float
    modified_at: str | None = None


def rrf_merge(*ranked: Sequence[str], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal rank fusion. Ranks start at 1; ties are ordered by key so results are deterministic."""
    score: dict[str, float] = {}
    for lst in ranked:
        for rank, key in enumerate(lst, start=1):
            score[key] = score.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def when_bounds(when: str, now: float) -> tuple[str | None, str | None]:
    """(not_before, not_after) as ISO strings to compare against items.modified_at."""
    if when == "any":
        return None, None
    if when == "today":
        return _iso(sgt_day_start(now)), None
    days = {"week": 7, "month": 30, "year": 365}
    if when in days:
        return _iso(now - days[when] * DAY), None
    if when == "older":
        return None, _iso(now - 365 * DAY)
    raise ValueError("WHEN")


def _ordered_unique(seq) -> list[str]:
    seen: dict[str, None] = {}
    for x in seq:
        seen.setdefault(x, None)
    return list(seen)


def search(ctx: Ctx, text: str, *, sources: list[str] | None = None, when: str = "any",
           limit: int = 10, exclude_private: bool = False) -> list[Hit]:
    """`exclude_private` drops items of chats the person keeps out of context packs. The app's own search leaves
    it off (they can find their own messages); ask-the-wiki and context() always turn it on."""
    if not text.strip():
        return []
    qvec = ctx.embedder.embed_query(text)
    vec_hits = ctx.index.vector_search(qvec)
    fts_hits = ctx.index.fts_search(text)

    def item_of(ref_id: str) -> str:
        return ref_id.rsplit(":", 1)[0]

    vec_items = _ordered_unique(h.item_id for h in vec_hits if h.item_id)
    fts_items = _ordered_unique(item_of(h.ref_id) for h in fts_hits)
    candidates = _ordered_unique(vec_items + fts_items)
    if not candidates:
        return []

    where = ["status != 'skipped'", f"id IN ({','.join('?' * len(candidates))})"]
    args: list = list(candidates)
    if sources:
        where.append("(" + " OR ".join(f"({SOURCE_FILTERS[s]})" for s in sources) + ")")
    if exclude_private:
        keys = list(ctx.settings.get("context_private_chats") or [])
        if keys:
            where.append(f"COALESCE(source || ':' || json_extract(meta, '$.chat_id'), '') "
                         f"NOT IN ({','.join('?' * len(keys))})")
            args += keys
    lo, hi = when_bounds(when, ctx.clock.now())
    if lo:
        where.append("modified_at >= ?")
        args.append(lo)
    if hi:
        where.append("modified_at < ?")
        args.append(hi)
    rows = {r["id"]: r for r in ctx.con.execute(
        f"SELECT id, title, locator, source, kind, modified_at FROM items WHERE {' AND '.join(where)}", args)}

    merged = rrf_merge([i for i in vec_items if i in rows], [i for i in fts_items if i in rows])[:limit]

    fts_snip: dict[str, str] = {}
    for h in fts_hits:
        fts_snip.setdefault(item_of(h.ref_id), h.snippet)
    best_chunk: dict[str, str] = {}
    for h in vec_hits:
        if h.item_id and h.ref_kind == "chunk":
            best_chunk.setdefault(h.item_id, h.ref_id)

    out: list[Hit] = []
    for item_id, score in merged:
        r = rows[item_id]
        snippet = fts_snip.get(item_id)
        if snippet is None:
            if r["kind"] == "image":
                snippet = "[image]"
            elif item_id in best_chunk:
                item, no = best_chunk[item_id].rsplit(":", 1)
                c = ctx.con.execute("SELECT text_scrubbed FROM item_chunks WHERE item_id=? AND chunk_no=?",
                                    (item, int(no))).fetchone()
                snippet = (c[0] if c else "")[:160]
            else:
                snippet = ""
        out.append(Hit(item_id, r["title"], r["locator"], r["source"], r["kind"], snippet, score, r["modified_at"]))
    return out
