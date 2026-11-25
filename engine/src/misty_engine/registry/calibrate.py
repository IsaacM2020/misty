"""Calibrate the resolution thresholds on the person's real names, with no model call (spec 3.2, Task 5.2).

Each grid point is one dry-run rebuild (judge verdicts only from the cache, everything rolled back) and three
measurements against the person's approved duplicate file:
  golden_complete_pct  share of golden groups whose spellings end as ONE entity, or as entities joined by a pair that
                       the judge would be asked (so "complete if the judge says same"). A spelling of one word that
                       belongs to two different people is ambiguous by nature and is not held against the group.
  negative_merges      golden "apart" pairs that ended as one entity. Must be 0.
  judge_pairs_needed   what the judge would cost."""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from ..ctx import Ctx
from . import sweep as S
from .names import core_tokens
from .rebuild import rebuild
from .resolve import AliasIndex
from .store import Registry


@dataclass
class GridRow:
    auto_cos: float
    judge_cos: float
    auto_jaccard: float
    golden_complete_pct: float
    negative_merges: int
    judge_pairs_needed: int
    auto_merges: int


class _CachedEmbedder:
    """Names are embedded once for the whole grid; a grid point only changes thresholds."""

    def __init__(self, inner):
        self.inner, self.cache = inner, {}

    def embed_documents(self, texts):
        miss = [t for t in dict.fromkeys(texts) if t not in self.cache]
        if miss:
            for t, v in zip(miss, self.inner.embed_documents(miss)):
                self.cache[t] = np.asarray(v, dtype=np.float32)
        return np.stack([self.cache[t] for t in texts]) if texts else np.zeros((0, 1), dtype=np.float32)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _entities_for(reg: Registry, spelling: str, kind: str) -> set[str]:
    key = " ".join(core_tokens(spelling, kind))
    out: set[str] = set()
    for r in reg.con.execute("SELECT entity_id FROM entity_aliases WHERE alias_norm=? AND kind=?", (key, kind)):
        s = reg.survivor(r[0])
        e = reg.get(s)
        if e is not None and e["status"] in ("candidate", "active"):
            out.add(s)
    return out


def measure(ctx: Ctx, dups: dict) -> tuple[float, int]:
    """(golden_complete_pct, negative_merges) on the registry as it stands right now."""
    pct, neg, _ = measure_full(ctx, dups)
    return pct, neg


def measure_full(ctx: Ctx, dups: dict) -> tuple[float, int, int]:
    """(golden_complete_pct, negative_merges, contradicted). A golden group whose spellings land on two people
    with conflicting full names ("Janaki" is Janaki Noolu in the data, not Janaki Kota) is contradicted by the
    data: the entities that conflict with the group's main one are left out and counted, not held against the
    thresholds."""
    from .resolve import conflict_live
    from .sweep import _entity_questions
    reg = Registry(ctx.con, ctx.clock)
    ix = AliasIndex.build(reg)
    qs, autos = _entity_questions(ctx, reg, ix, sorted(r["id"] for r in reg.entities()))
    edges = {frozenset((q.a.key, q.b.key)) for q in qs} | {frozenset((a, b)) for a, b, _ in autos}
    groups = dups.get("groups") or []
    done = total = contradicted = 0
    for g in groups:
        kind = g.get("kind", "person")
        sets = []
        for m in g.get("members", []):
            ents = _entities_for(reg, m, kind)
            if len(ents) > 1 and len(core_tokens(m, kind)) == 1:
                continue                         # one word that is two different things: ambiguous, not a miss
            if ents:
                sets.append(ents)
        union = set().union(*sets) if sets else set()
        if len(union) > 1:
            votes: dict[str, int] = {}
            for s in sets:
                for e in s:
                    votes[e] = votes.get(e, 0) + 1
            core = max(sorted(union), key=lambda e: votes[e])
            keep = {e for e in union if e == core or not conflict_live(reg, core, e)}
            if keep != union:
                contradicted += 1
            union = keep
        if len(union) <= 1:
            ok = True
        else:                                    # connected by pairs the judge would be asked?
            seen, stack = set(), [next(iter(union))]
            while stack:
                x = stack.pop()
                if x in seen:
                    continue
                seen.add(x)
                stack += [y for y in union if y not in seen and frozenset((x, y)) in edges]
            ok = seen >= union
        total += 1
        done += ok
    neg = 0
    for a, b in dups.get("apart") or []:
        ka = _entities_for(reg, a, "person")
        kb = _entities_for(reg, b, "person")
        neg += bool(ka and kb and (ka & kb))
    return (100.0 * done / total if total else 100.0), neg, contradicted


async def calibrate(ctx: Ctx, dups: dict, grid: dict) -> list[GridRow]:
    """One row per point of grid["auto_cos"] x grid["judge_cos"] x grid["auto_jaccard"]."""
    rows: list[GridRow] = []
    real_embedder = ctx.embedder
    ctx.embedder = _CachedEmbedder(real_embedder)
    try:
        for ac, jc, aj in itertools.product(grid["auto_cos"], grid["judge_cos"], grid["auto_jaccard"]):
            if jc > ac:
                continue                          # the question band must sit below the automatic one
            S.OVERRIDES.update({"auto_cos": ac, "judge_cos": jc, "auto_jaccard": aj})
            try:
                rep = await rebuild(ctx, dry_run=True, cache_only=True, probe=lambda c, r: measure(c, dups))
            finally:
                S.OVERRIDES.clear()
            pct, neg = rep.extra["probe"]
            rows.append(GridRow(ac, jc, aj, round(pct, 1), neg, rep.judge_pairs_needed,
                                sum(rep.merges.values())))
    finally:
        ctx.embedder = real_embedder
    return rows


def choose(rows: list[GridRow], min_pct: float = 90.0) -> GridRow | None:
    """No negative merges, golden completeness at least min_pct, fewest judge pairs, ties to the higher auto_cos."""
    ok = [r for r in rows if r.negative_merges == 0 and r.golden_complete_pct >= min_pct]
    return min(ok, key=lambda r: (r.judge_pairs_needed, -r.auto_cos, -r.judge_cos, -r.auto_jaccard)) if ok else None
