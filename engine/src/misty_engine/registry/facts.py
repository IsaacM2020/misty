"""Structured facts about an entity (spec 3.6): what an actions agent needs, with dates, none of it prose.

Everything here is deterministic. It reads the entity's public notes, the pending suggestions that mention it and
the registry; the model-written parts (relationship, role, status) come from `entity_text`. Contact handles are
returned by `handles()` for local callers only and never reach a page or a prompt."""
from __future__ import annotations

import json
from collections import Counter

from ..clock import sgt_date
from ..ctx import Ctx
from .store import Registry


def _public(ctx: Ctx, eid: str):
    from . import render
    return render._public_items(ctx, eid)


def open_items(ctx: Ctx, eid: str) -> list[dict]:
    """Pending suggestions (new or snoozed) whose note mentions the entity, newest first."""
    ids = [r["id"] for r in _public(ctx, eid)]
    out = []
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        for r in ctx.con.execute(
                f"SELECT kind, text, item_id FROM suggestions WHERE status IN ('new','snoozed') AND item_id IN "
                f"({','.join('?' * len(chunk))}) ORDER BY created_at DESC, id", chunk):
            out.append({"kind": r["kind"], "text": r["text"], "item_id": r["item_id"]})
    return out


def handles(ctx: Ctx, eid: str) -> list[dict]:
    return [{"type": t, "value": v} for t, v in Registry(ctx.con, ctx.clock).handles(eid)]


def _collaborators(ctx: Ctx, eid: str, item_ids: list[str]) -> list[str]:
    counts: Counter = Counter()
    for i in range(0, len(item_ids), 400):
        chunk = item_ids[i:i + 400]
        for r in ctx.con.execute(
                f"SELECT e.name AS name, COUNT(DISTINCT m.item_id) AS n FROM mentions m JOIN entities e "
                f"ON e.id = m.entity_id WHERE m.item_id IN ({','.join('?' * len(chunk))}) AND e.kind='person' "
                f"AND e.status IN ('candidate','active') AND m.entity_id != ? GROUP BY e.id", (*chunk, eid)):
            counts[r["name"]] += r["n"]
    return [n for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if c >= 2][:3]


def compute(ctx: Ctx, eid: str) -> dict:
    """Facts for one entity. Keys with nothing to say are None (and left out of a page's frontmatter)."""
    reg = Registry(ctx.con, ctx.clock)
    e = reg.get(eid)
    today = sgt_date(ctx.clock.now())
    items = _public(ctx, eid)
    past: list[str] = []
    future: list[str] = []
    for r in items:
        meta = json.loads(r["meta"] or "{}")
        if r["modified_at"] and r["modified_at"][:10] <= today:       # a note's own date is never a future event
            past.append(r["modified_at"][:10])
        for d, _ in meta.get("dated_lines") or []:
            d = str(d)[:10]
            (past if d <= today else future).append(d)
    t = ctx.con.execute("SELECT * FROM entity_text WHERE entity_id=?", (eid,)).fetchone()
    f = {
        "relationship": t["relationship"] if t else None,
        "role": t["role"] if t else None,
        "status": t["status_label"] if t else None,
        "last_interaction": max(past) if past else None,
        "first_seen": min(past + future) if (past or future) else None,
        "next_date": min(future) if future else None,
        "open_items": len(open_items(ctx, eid)),
        "as_of": today,
        "summary_as_of": sgt_date(t["summarised_at"]) if t and t["summarised_at"] else None,
    }
    if e["kind"] in ("project", "topic"):
        f["collaborators"] = _collaborators(ctx, eid, [r["id"] for r in items])
    return f
