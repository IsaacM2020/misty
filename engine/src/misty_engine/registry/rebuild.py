"""Bring an existing wiki into the registry, and clean it with the same code that resolves a live sweep (spec 3.13).

`import_existing`  every entity page becomes an entity (real summaries kept, stubs become candidates).
`find_classes`     groups of entities that may be one thing (counts, for reports and the judge).
`rebuild`          import, re-resolve every note's names, judge only what stays ambiguous, merge with snapshots.
A dry run does all of it inside a savepoint that is rolled back and asks the model nothing: counts out."""
from __future__ import annotations

import json
import math
import re

from ..ctx import Ctx
from ..wiki.notes import section
from .names import norm, reject_reason
from .resolve import AliasIndex, conflict_live
from .store import Registry
from .sweep import SweepReport, _entity_questions, cut_bridges, qualifies, resolve_sweep

ENTITY_KINDS = {"person": "people", "project": "projects", "topic": "topics"}
_STUB = {"", "(stub)"}


def _bullets(text: str) -> list[str]:
    return [l[2:].strip() for l in (text or "").splitlines() if l.startswith("- ") and l[2:].strip()][:5]


def _live_by_kind(ctx: Ctx) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in ctx.con.execute("SELECT kind, COUNT(*) n FROM entities WHERE status IN ('candidate','active') "
                             "GROUP BY kind"):
        out[r["kind"]] = r["n"]
    return out


def import_existing(ctx: Ctx) -> dict:
    """One entity per entity page. A page with a real summary becomes an `active` entity that keeps its text and
    path; a stub page becomes a `candidate` (no page, no text) with a redirect from its old path. Names the
    resolver would reject (a chat member, a phone number) are not imported. Safe to run twice."""
    reg = Registry(ctx.con, ctx.clock)
    out = {"active": 0, "candidate": 0, "already": 0, "rejected": {}, "pages": 0}
    now = ctx.clock.now()
    known_ids = {r[0] for r in ctx.con.execute("SELECT id FROM entities")}
    known_paths = {r[0] for r in ctx.con.execute("SELECT path FROM entities WHERE path IS NOT NULL")} | \
                  {r[0] for r in ctx.con.execute("SELECT old_path FROM entity_redirects")}
    from ..db import tx
    with tx(ctx.con):
        for rel in ctx.wiki.all_pages():
            folder = rel.split("/")[0]
            if folder not in ENTITY_KINDS.values():
                continue
            page = ctx.wiki.read(rel)
            if page is None or page.fm.get("type") not in ENTITY_KINDS:
                continue
            out["pages"] += 1
            kind, title = page.fm["type"], str(page.fm.get("title") or "").strip()
            if page.fm.get("entity_id") in known_ids or rel in known_paths:
                out["already"] += 1
                continue
            code = reject_reason(title, kind)
            if code:
                out["rejected"][code] = out["rejected"].get(code, 0) + 1
                continue
            summary = section(page.body, "Summary").strip()
            real = summary not in _STUB
            eid = reg.new_entity(kind, title, origin="migration", status="active" if real else "candidate",
                                 path=rel if real else None)
            for alias in [title, *[a for a in page.fm.get("aliases") or [] if isinstance(a, str)]]:
                reg.add_alias(eid, alias, source="migration", n=0)
            area = page.fm.get("area")
            imp = page.fm.get("importance")
            ctx.con.execute("UPDATE entities SET area=?, importance=? WHERE id=?",
                            (area if isinstance(area, str) else None, imp if isinstance(imp, int) else 3, eid))
            if real:
                ctx.con.execute(
                    "INSERT INTO entity_text(entity_id, summary, now, summary_n, summarised_at, model, prompt_v) "
                    "VALUES (?,?,?,?,?,?,1)",       # prompt_v 1: a kept summary is current, not stale (Q5 compares)
                    (eid, summary, json.dumps(_bullets(section(page.body, "Now"))),
                     len(page.fm.get("sources") or []), now, "migration"))
                out["active"] += 1
            else:
                ctx.con.execute("INSERT OR REPLACE INTO entity_redirects(old_path, entity_id) VALUES (?,?)", (rel, eid))
                out["candidate"] += 1
    return out


def find_classes(ctx: Ctx) -> list[list[str]]:
    """Groups of 2 or more live entities of one kind that may be one thing (token-compatible people, near
    projects and topics). A group never joins two entities whose full names conflict."""
    reg = Registry(ctx.con, ctx.clock)
    ix = AliasIndex.build(reg)
    qs, autos = _entity_questions(ctx, reg, ix, sorted(r["id"] for r in reg.entities()))
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    members: dict[str, set[str]] = {}
    pairs = cut_bridges(reg, [(qq.a.key, qq.b.key) for qq in qs] + [(a, b) for a, b, _ in autos])
    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        ga, gb = members.setdefault(ra, {ra}), members.setdefault(rb, {rb})
        if any(conflict_live(reg, x, y) for x in ga for y in gb):
            continue
        parent[rb] = ra
        members[ra] = ga | gb
    groups = {}
    for x in list(parent):
        groups.setdefault(find(x), set()).add(x)
    return sorted((sorted(g) for g in groups.values() if len(g) >= 2), key=lambda g: (-len(g), g))


def estimate_judge_usd(ctx: Ctx, pairs: int) -> float:
    """Haiku price for `pairs` pairs: 20 a call, about 130 tokens in and 40 out per pair, plus the fixed prompt."""
    calls = math.ceil(pairs / 20)
    p = ctx.pinned.prices
    tin, tout = calls * 450 + pairs * 130, calls * 20 + pairs * 40
    return round((tin * p.haiku_in + tout * p.haiku_out) / 1_000_000, 5)


def _pages_estimate(ctx: Ctx, reg: Registry, classes: list[list[str]]) -> dict:
    """How many entities would have a page: a real summary already, or 2 public sources, or a known name.
    `now` counts entities as they are; `floor` first merges every open class (the judge saying same to all)."""
    from .sweep import is_known, public_source_count
    ents = {r["id"]: r for r in reg.entities()}
    in_class = {e for g in classes for e in g}
    groups = [g for g in classes] + [[e] for e in ents if e not in in_class]

    def has_page(g: list[str]) -> bool:
        for e in g:
            row = ents[e]
            t = ctx.con.execute("SELECT summary FROM entity_text WHERE entity_id=?", (e,)).fetchone()
            if row["status"] == "active" and t and (t["summary"] or "").strip() not in ("", "(stub)"):
                return True
            if is_known(ctx, reg, row):
                return True
        items: set[str] = set()
        for e in g:
            items |= {r[0] for r in ctx.con.execute("SELECT DISTINCT item_id FROM mentions WHERE entity_id=?", (e,))}
        if len(items) < 2:
            return False
        return any(public_source_count(ctx, e) >= 1 for e in g) and len(items) >= 2

    now = floor = short = 0
    for e in ents:
        ok = has_page([e])
        now += ok
        short += (not ok and ents[e]["status"] == "candidate")
    floor = sum(1 for g in groups if has_page(g))
    return {"now": now, "floor": floor, "candidates_short": short}


def _page_count(ctx: Ctx) -> int:
    return sum(1 for rel in ctx.wiki.all_pages()
               if rel.split("/")[0] in ENTITY_KINDS.values() and (ctx.wiki.read(rel) or None))


async def rebuild(ctx: Ctx, *, dry_run: bool = True, cache_only: bool = True, probe=None,
                  build_id: str | None = None) -> SweepReport:
    """Import, re-resolve every note, judge the ambiguous pairs, merge. Dry run: nothing is kept, no model is
    called. Counts in `report.extra`: entities before and after by kind (and the floor if every open pair were
    the same thing), pages before and an estimate after, the judge's price."""
    cache_only = cache_only or dry_run
    if dry_run:
        ctx.con.execute("SAVEPOINT misty_rebuild")
    try:
        pages_before = _page_count(ctx)
        imp = import_existing(ctx)
        before = _live_by_kind(ctx)
        rep = await resolve_sweep(ctx, rebuild=True, cache_only=cache_only, _in_savepoint=dry_run,
                                  build_id=build_id)
        after = _live_by_kind(ctx)
        classes = find_classes(ctx)
        reg = Registry(ctx.con, ctx.clock)
        pages = _pages_estimate(ctx, reg, classes)
        rep.extra = {
            "pages_before": pages_before, "imported": {k: v for k, v in imp.items() if k != "rejected"},
            "import_rejected": imp["rejected"], "entities_before": before, "entities_after": after,
            "open_classes": len(classes),
            "pages_after_estimate": pages["now"],                 # as things stand (open pairs stay apart)
            "pages_after_if_all_open_pairs_same": pages["floor"],  # a floor: every open pair judged the same
            "candidates_not_enough_sources": pages["candidates_short"],
            "estimated_judge_usd": estimate_judge_usd(ctx, rep.judge_pairs_needed),
        }
        if probe is not None:                     # look at the registry before the dry run is rolled back
            rep.extra["probe"] = probe(ctx, rep)
        return rep
    finally:
        if dry_run:
            ctx.con.execute("ROLLBACK TO misty_rebuild")
            ctx.con.execute("RELEASE misty_rebuild")
