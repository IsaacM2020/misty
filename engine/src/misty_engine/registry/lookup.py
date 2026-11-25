"""entities.lookup (spec 3.9): "who is Ms Janaki?" answered from the registry, with a confidence and an honest
"ambiguous" when two candidates are close. Local only: names, aliases and name vectors, no model."""
from __future__ import annotations

import numpy as np

from ..ctx import Ctx
from . import facts as F
from .names import compatible, core_tokens
from .resolve import AliasIndex
from .store import Registry

AMBIGUOUS_GAP = 0.15
MIN_CONF = 0.5
VEC_FLOOR = 0.6


def _candidate(ctx: Ctx, reg: Registry, eid: str, conf: float, why: str) -> dict:
    e = reg.get(eid)
    f = F.compute(ctx, eid)
    f["handles"] = F.handles(ctx, eid)           # local callers only; never put into a prompt by this layer
    return {"id": eid, "name": e["name"], "type": e["kind"], "facts": f, "confidence": round(conf, 3), "why": why}


def lookup(ctx: Ctx, text: str, kind: str | None = None, k: int = 5) -> dict:
    reg = Registry(ctx.con, ctx.clock)
    text = " ".join((text or "").split())
    if not text:
        return {"candidates": [], "ambiguous": False}
    kinds = [kind] if kind else ["person", "project", "topic"]
    ix = AliasIndex.build(reg)
    scored: dict[str, tuple[float, str]] = {}

    def offer(eid: str, conf: float, why: str) -> None:
        if eid not in scored or conf > scored[eid][0]:
            scored[eid] = (conf, why)

    for kd in kinds:
        core = core_tokens(text, kd)
        key = " ".join(core)
        if not core:
            continue
        exact = ix.find(kd, key)
        for eid in exact:
            if len(core) >= 2:
                offer(eid, 0.95, "exact name")
            else:
                offer(eid, 0.8 if reg.mention_count(eid) >= 2 else 0.6, "one-word name")
        if kd == "person":
            for eid in ix.person_candidates(core):
                if eid in exact:
                    continue
                if len(core) >= 2:
                    offer(eid, 0.8, "fuller or shorter spelling of the same name")
                else:
                    offer(eid, 0.8 if reg.mention_count(eid) >= 2 else 0.6, "shares the first or last name")
    if ctx.embedder is not None and len(text) >= 3:          # near names (topics and projects especially)
        qv = np.asarray(ctx.embedder.embed_documents([text])[0], dtype=np.float32)
        q = ("SELECT v.entity_id, v.vec FROM entity_vec v JOIN entities e ON e.id = v.entity_id "
             "WHERE e.status IN ('candidate','active')" + (" AND e.kind=?" if kind else ""))
        for r in ctx.con.execute(q, ([kind] if kind else [])):
            cos = float(np.dot(qv, np.frombuffer(r["vec"], dtype=np.float32)))
            if cos >= VEC_FLOOR and r["entity_id"] not in scored:
                scored[r["entity_id"]] = (0.5 * cos, f"similar name (cosine {cos:.2f})")
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1][0], reg.get(kv[0])["name"].lower(), kv[0]))[:max(1, k)]
    cands = [_candidate(ctx, reg, eid, conf, why) for eid, (conf, why) in ranked]
    ambiguous = (len(cands) >= 2 and cands[0]["confidence"] - cands[1]["confidence"] <= AMBIGUOUS_GAP
                 and cands[0]["confidence"] >= MIN_CONF and cands[1]["confidence"] >= MIN_CONF)
    return {"candidates": cands, "ambiguous": ambiguous}
