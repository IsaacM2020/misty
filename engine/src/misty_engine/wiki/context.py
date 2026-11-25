"""Context packs (spec 3.9, v2): what Misty core pastes into a prompt about a topic, within a token budget.

One entry per entity, whatever the spelling or the merged path. Deterministic. Nothing from a chat the person keeps
out of packs unless the caller asks for it by name. Registry entities are rendered from the registry; a wiki that
has not been migrated yet still works, page by page, as one pseudo-entity per file."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..clock import sgt_date
from ..ctx import Ctx
from ..search import rrf_merge, search
from ..types import approx_tokens
from .notes import links_in, section

ENTITY_TYPES = ("person", "project", "topic", "area", "answer")
MIN_VEC_SIM = 0.45
OPEN_NOW_MAX = 8
HOP_MAX = 6
ENTITY_BOOST = 0.5


@dataclass
class PackEntity:
    id: str
    name: str
    type: str
    path: str
    summary: str
    now: list[str]
    facts: dict
    freshness_days: int | None
    why: str


@dataclass
class ContextPack:
    text: str
    pages: list[str]
    tokens: int
    sources_used: list[str] = field(default_factory=list)
    entities: list[PackEntity] = field(default_factory=list)
    open_now: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)


DATED_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}):", re.M)


def freshness(ctx: Ctx, body: str) -> float:
    """Weight by the newest dated Timeline line, so a page that went quiet (finished exams, old projects)
    ranks below a current page with the same words. Undated pages sit in the middle."""
    dates = DATED_RE.findall(section(body, "Timeline"))
    if not dates:
        return 0.5
    age = (date.fromisoformat(sgt_date(ctx.clock.now())) - date.fromisoformat(max(dates))).days
    return 1.0 if age <= 45 else 0.75 if age <= 120 else 0.5 if age <= 365 else 0.3


def match_pages(ctx: Ctx, topic: str, k: int = 6) -> list[tuple[str, float]]:
    if not topic.strip() or ctx.index is None:
        return []
    qvec = ctx.embedder.embed_query(topic)
    # KNN always returns neighbours, related or not, so a vector-only hit has to be actually similar
    vec = [h.ref_id for h in ctx.index.vector_search(qvec, k=60, ref_kinds=("page",)) if h.sim >= MIN_VEC_SIM]
    fts = [h.ref_id for h in ctx.index.fts_search(topic, k=60, ref_kinds=("page",))]
    out = []
    for path, score in rrf_merge(vec, fts):
        if path.startswith("sources/"):
            continue
        p = ctx.wiki.read(path)
        if p is not None:
            out.append((path, score * freshness(ctx, p.body)))
    out.sort(key=lambda t: (-t[1], t[0]))
    return out[:k]


def _toks(s: str) -> int:
    return approx_tokens(s) + 1


# ------------------------------------------------------------------ one entry per entity

@dataclass
class _Entry:
    id: str
    path: str
    name: str
    type: str
    summary: str
    now: list[str]
    timeline: list[str]
    facts: dict
    freshness_days: int | None
    importance: int
    updated: str
    items: list[str]
    why: str = "matched the topic"
    legacy_links: list[str] = field(default_factory=list)


def _days_since(ctx: Ctx, iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return (date.fromisoformat(sgt_date(ctx.clock.now())) - date.fromisoformat(iso[:10])).days
    except ValueError:
        return None


def _load(ctx: Ctx, path: str, include_private: bool) -> _Entry | None:
    from ..registry import facts as F
    from ..registry import render
    from ..registry.store import Registry
    reg = Registry(ctx.con, ctx.clock)
    target = reg.redirect(path)                         # a merged page's old path is its survivor
    row = reg.get(target) if target else ctx.con.execute(
        "SELECT * FROM entities WHERE path=? AND status='active'", (path,)).fetchone()
    if row is not None and row["status"] == "active" and row["path"]:
        t = ctx.con.execute("SELECT * FROM entity_text WHERE entity_id=?", (row["id"],)).fetchone()
        items = render._public_items(ctx, row["id"], include_private)
        today = sgt_date(ctx.clock.now())
        tl = render._timeline(ctx, reg, row, items, today)
        f = F.compute(ctx, row["id"])
        f["handles"] = F.handles(ctx, row["id"])
        return _Entry(
            id=row["id"], path=row["path"], name=row["name"], type=row["kind"],
            summary=(t["summary"] or "").strip() if t else "",
            now=[b for b in json_list(t["now"]) if b.strip()] if t else [], timeline=tl, facts=f,
            freshness_days=_days_since(ctx, f.get("last_interaction")), importance=int(row["importance"] or 3),
            updated=sgt_date(t["summarised_at"]) if t and t["summarised_at"] else "", items=[r["id"] for r in items])
    p = ctx.wiki.read(path)                              # a page the registry does not own (an unmigrated wiki)
    if p is None or p.fm.get("type") not in ENTITY_TYPES:
        return None
    dates = DATED_RE.findall(section(p.body, "Timeline"))
    summary = section(p.body, "Summary").strip()
    return _Entry(
        id=path, path=path, name=str(p.fm.get("title", path)), type=str(p.fm.get("type")),
        summary="" if summary == "(stub)" else summary,
        now=[l[2:].strip() for l in section(p.body, "Now").splitlines() if l.startswith("- ")],
        timeline=[l for l in section(p.body, "Timeline").splitlines() if l.startswith("- ")], facts={},
        freshness_days=_days_since(ctx, max(dates) if dates else None),
        importance=int(p.fm.get("importance", 3) or 3), updated=str(p.fm.get("updated", "")),
        items=list(p.fm.get("sources") or []), legacy_links=[l + ".md" for l in links_in(p.body)
                                                              if not l.startswith("sources/")])


def json_list(raw: str | None) -> list[str]:
    import json
    try:
        v = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def _last_activity(e: _Entry) -> str:
    """The date something last happened to this entity (not the day its page was last redrawn)."""
    li = e.facts.get("last_interaction")
    if li:
        return str(li)[:10]
    dates = DATED_RE.findall("\n".join(e.timeline))
    return max(dates) if dates else ""


def _block(e: _Entry) -> list[str]:
    last = _last_activity(e)
    lines = [f"## [[{e.path[:-3]}]] ({e.type}" + (f" · last activity {last})" if last else ")")]
    if e.summary:
        lines.append(f"Summary: {e.summary}")
    if e.now:
        lines.append("Now:\n" + "\n".join(f"- {b}" for b in e.now))
    return lines


def _open_now(ctx: Ctx, entries: list[_Entry], include_private: bool) -> list[dict]:
    """What is open right now: pending cards on the matched entities' notes, dated lines from today to a week
    out, and the Now bullets of the matched entities. At most OPEN_NOW_MAX, deterministic order."""
    today = date.fromisoformat(sgt_date(ctx.clock.now()))
    hi = (today + timedelta(days=7)).isoformat()
    out: list[dict] = []
    item_ids = sorted({i for e in entries for i in e.items})
    for i in range(0, len(item_ids), 400):
        chunk = item_ids[i:i + 400]
        for r in ctx.con.execute(
                f"SELECT kind, text, item_id FROM suggestions WHERE status IN ('new','snoozed') AND item_id IN "
                f"({','.join('?' * len(chunk))}) ORDER BY created_at DESC, id", chunk):
            out.append({"kind": "card", "text": r["text"], "due": None, "item_id": r["item_id"]})
    for e in entries:
        for line in e.timeline:
            m = re.match(r"^- (\d{4}-\d{2}-\d{2}): (.*?)(?: \(\[\[sources/[^\]]*\]\]\))?$", line)
            if m and today.isoformat() <= m[1] <= hi:
                out.append({"kind": "date", "text": f"{e.name}: {m[2]}", "due": m[1], "item_id": None})
        for b in e.now:
            out.append({"kind": "now", "text": f"{e.name}: {b}", "due": None, "item_id": None})
    seen, uniq = set(), []
    for o in out:
        k = (o["kind"], o["text"])
        if k not in seen:
            seen.add(k)
            uniq.append(o)
    uniq.sort(key=lambda o: ({"card": 0, "date": 1, "now": 2}[o["kind"]], o["due"] or "9999", o["text"]))
    return uniq[:OPEN_NOW_MAX]


def build_context(ctx: Ctx, topic: str, budget_tokens: int = 2000, *, entities: list[str] | None = None,
                  include_private: bool = False) -> ContextPack:
    if budget_tokens <= 0:
        return ContextPack("", [], 0, [])
    from ..registry.store import Registry
    reg = Registry(ctx.con, ctx.clock)
    if include_private:
        from ..logs import log_event
        log_event("CONTEXT_PRIVATE", n=1)             # by call, never by content
    # 1. matched entities, one per entity id (merged paths resolve to their survivor), by score then id
    scored: dict[str, tuple[float, _Entry]] = {}

    def take(path: str, score: float, why: str) -> None:
        e = _load(ctx, path, include_private)
        if e is None:
            return
        e.why = why
        if e.id not in scored or score > scored[e.id][0]:
            scored[e.id] = (score, e)

    for path, score in match_pages(ctx, topic):
        take(path, score, "matched the topic")
    for eid in entities or []:
        row = reg.get(reg.survivor(eid))
        if row is not None and row["path"] and row["status"] == "active":
            e = _load(ctx, row["path"], include_private)
            if e is not None:
                prev = scored.get(e.id, (0.0, e))[0]
                e.why = "named in the request"
                scored[e.id] = (prev + ENTITY_BOOST, e)
    selected = [e for _, e in sorted(scored.values(), key=lambda t: (-t[0], t[1].id))]

    # 2. one hop: entities that share notes with the matched ones (registry) or are linked (unmigrated pages)
    hop: Counter = Counter()
    hop_entries: dict[str, _Entry] = {}
    chosen = {e.id for e in selected}
    for e in selected:
        if e.id.startswith("en_"):
            ids = e.items
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                for r in ctx.con.execute(
                        f"SELECT x.id AS eid, x.path AS path, x.importance AS imp, COUNT(DISTINCT m.item_id) AS n "
                        f"FROM mentions m JOIN entities x ON x.id = m.entity_id "
                        f"WHERE m.item_id IN ({','.join('?' * len(chunk))}) "
                        f"AND x.status='active' AND x.path IS NOT NULL AND x.importance >= 3 GROUP BY x.id", chunk):
                    if r["eid"] not in chosen:
                        hop[r["eid"]] += r["n"]
                        hop_entries.setdefault(r["eid"], _Entry(r["eid"], r["path"], "", "", "", [], [], {}, None,
                                                                int(r["imp"] or 3), "", []))
        else:
            for tgt in e.legacy_links:
                if tgt in {s.path for s in selected}:
                    continue
                x = _load(ctx, tgt, include_private)
                if x is not None and x.importance >= 3 and x.id not in chosen:
                    hop[x.id] += 1
                    hop_entries[x.id] = x
    # Rank the cheap stubs first (importance, shared-note count and path are already known) and fully load only as
    # many as HOP_MAX keeps. Loading re-reads and parses pages, and a busy note can have 100 neighbours.
    ranked: list[_Entry] = []
    for hid in sorted(hop, key=lambda i: (-hop_entries[i].importance, -hop[i], hop_entries[i].path)):
        if len(ranked) >= HOP_MAX:
            break
        h = hop_entries[hid]
        x = h if h.name else _load(ctx, h.path, include_private)
        if x is not None and x.importance >= 3:
            ranked.append(x)
    for x in ranked:
        x.why = "shares notes with a matched entity"
    all_entries = selected + ranked

    # 3. budget: the about card is the first thing cut; entity blocks, then timelines round-robin, then raw chunks
    about = ctx.card()
    header = "## about-isaac\n"
    def open_block_for(rows: list[dict]) -> str:
        return ("## open now\n" + "\n".join(
            f"- {o['text']}" + (f" (due {o['due']})" if o["due"] else "") for o in rows)) if rows else ""

    def assemble(about_tokens: int, open_block: str) -> tuple[str, list[_Entry], list[dict], list[str]]:
        used = ["about"] if about_tokens > 0 else []
        blocks = []
        if about_tokens > 0:
            blocks.append(header + about[: max(0, about_tokens * 4 - len(header))])
        total = _toks(blocks[0]) if blocks else 0
        if open_block and total + _toks(open_block) <= budget_tokens:
            blocks.append(open_block)
            total += _toks(open_block)
            used.append("open_now")
        heads: list[list[str]] = []
        tls: list[list[str]] = []
        kept: list[_Entry] = []
        dropped: list[dict] = []
        for e in all_entries:
            lines = _block(e)
            cost = sum(_toks(l) for l in lines)
            if total + cost > budget_tokens:
                dropped.append({"id": e.id, "reason": "budget"})
                continue
            total += cost
            heads.append(lines)
            tls.append(e.timeline)
            kept.append(e)
        if kept:
            used.append("pages")
        if any(e in ranked for e in kept):
            used.append("hop")
        longest = max((len(t) for t in tls), default=0)
        added = False
        for r in range(longest):
            for i, tl in enumerate(tls):
                if r < len(tl) and total + _toks(tl[r]) <= budget_tokens:
                    if not any(l.startswith("Timeline:") for l in heads[i]):
                        if total + _toks("Timeline:") + _toks(tl[r]) > budget_tokens:
                            continue
                        heads[i].append("Timeline:")
                        total += _toks("Timeline:")
                    heads[i].append(tl[r])
                    total += _toks(tl[r])
                    added = True
        if added:
            used.append("timeline")
        blocks += ["\n".join(h) for h in heads]
        # raw chunks last, only for notes no included entity already carries, and only under half the budget
        if total < 0.5 * budget_tokens and topic.strip():
            represented = {i for e in kept for i in e.items}
            extra = []
            for h in search(ctx, topic, limit=5, exclude_private=not include_private):
                if h.item_id in represented:
                    continue
                line = f"- ({h.source}) {h.title}: {' '.join(h.snippet.split())}"
                if total + _toks(line) + _toks("## search results") > 0.5 * budget_tokens:
                    break
                extra.append(line)
                total += _toks(line)
            if extra:
                blocks.append("## search results\n" + "\n".join(extra))
                used.append("search_chunks")
        return "\n\n".join(blocks), kept, dropped, used

    about_room = min(_toks(header + about), max(0, budget_tokens // 4))

    def pack_with(rows: list[dict]):
        block = open_block_for(rows)
        r = assemble(about_room, block)
        if all_entries and not r[1] and about_room:          # not even one entity fits: the card gives way first
            r = assemble(0, block)
        return r

    # "open now" lists only what belongs to entities that really made it into the pack, so it is settled in a
    # few rounds: build, see who was kept, rebuild the list from them (a shorter list can only let more fit)
    open_now = _open_now(ctx, selected, include_private)
    for _ in range(3):
        text, kept, dropped, used = pack_with(open_now)
        kept_ids = {e.id for e in kept}
        again = _open_now(ctx, [e for e in selected if e.id in kept_ids], include_private)
        if again == open_now:
            break
        open_now = again
    else:
        text, kept, dropped, used = pack_with(open_now)
    if approx_tokens(text) > budget_tokens:
        text = text[: budget_tokens * 4]
    pe = [PackEntity(e.id, e.name, e.type, e.path, e.summary, e.now, e.facts, e.freshness_days, e.why) for e in kept]
    return ContextPack(text=text, pages=[e.path for e in kept], tokens=approx_tokens(text), sources_used=used,
                       entities=pe, open_now=open_now, dropped=dropped)
