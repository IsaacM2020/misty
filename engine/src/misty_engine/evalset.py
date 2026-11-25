"""Golden set: Isaac's own queries with the answer he would call right. Reports top-5 hit rate and latency."""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .ctx import Ctx
from .errors import Fatal
from .search import search

TARGET_TOP5 = 0.80
TARGET_P50_MS = 1000


@dataclass
class Case:
    id: str
    kind: str
    q: str
    expect_any: list[str] = field(default_factory=list)
    expect_cites_any: list[str] = field(default_factory=list)
    expect_answer_any: list[str] = field(default_factory=list)
    expect_entities: list[str] = field(default_factory=list)   # entity paths: any one in the top 5 / in the pack
    reference: str = ""                                        # ask: short reference answer for the judge
    must_not_cite: list[str] = field(default_factory=list)


@dataclass
class Report:
    top5_rate: float = 0.0
    ask_rate: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    n_search: int = 0
    n_ask: int = 0
    misses: list[str] = field(default_factory=list)


def load(path: Path) -> list[Case]:
    raw = yaml.safe_load(Path(path).read_text()) or []
    if not isinstance(raw, list):
        raise Fatal("GOLDEN_FORMAT")
    out, seen = [], set()
    for r in raw:
        if not isinstance(r, dict) or not r.get("id") or r["id"] in seen:
            raise Fatal("GOLDEN_ID")
        seen.add(r["id"])
        kind = r.get("kind")
        if kind not in ("search", "ask", "pack") or not str(r.get("q", "")).strip():
            raise Fatal("GOLDEN_KIND")
        c = Case(id=str(r["id"]), kind=kind, q=str(r["q"]), expect_any=list(r.get("expect_any") or []),
                 expect_cites_any=list(r.get("expect_cites_any") or []),
                 expect_answer_any=list(r.get("expect_answer_any") or []),
                 expect_entities=list(r.get("expect_entities") or []),
                 reference=str(r.get("reference") or ""),
                 must_not_cite=list(r.get("must_not_cite") or []))
        if kind == "search" and not (c.expect_any or c.expect_entities):
            raise Fatal("GOLDEN_EMPTY_EXPECT")
        if kind == "ask" and not (c.expect_cites_any or c.expect_answer_any or c.reference):
            raise Fatal("GOLDEN_EMPTY_EXPECT")
        if kind == "pack" and not c.expect_entities:
            raise Fatal("GOLDEN_EMPTY_EXPECT")
        out.append(c)
    return out


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def search_hit(ctx: Ctx, case: Case) -> tuple[bool, float]:
    t = time.perf_counter()
    hits = search(ctx, case.q, limit=5)
    ms = (time.perf_counter() - t) * 1000
    notes = {r[0]: (r[1] or "") for r in ctx.con.execute(
        f"SELECT id, note_path FROM items WHERE id IN ({','.join('?' * len(hits))})", [h.item_id for h in hits])} \
        if hits else {}
    hay = [f"{h.title}\n{h.locator}\n{notes.get(h.item_id, '')}".lower() for h in hits]
    ok = any(e.lower() in h for e in case.expect_any for h in hay)
    return ok, ms


async def run(ctx: Ctx, cases: list[Case], kind: str | None = None) -> Report:
    from .wiki.ask import ask_wiki
    rep = Report()
    lat: list[float] = []
    s_ok = a_ok = 0
    for c in cases:
        if (kind and c.kind != kind) or c.kind == "pack" or (c.kind == "search" and not c.expect_any):
            continue                  # pack and entity-only cases belong to the Q5 eval harness
        if c.kind == "search":
            rep.n_search += 1
            ok, ms = search_hit(ctx, c)
            lat.append(ms)
            s_ok += ok
        else:
            rep.n_ask += 1
            r = await ask_wiki(ctx, c.q, job_id=None)
            ok = (any(x in r.citations for x in c.expect_cites_any) if c.expect_cites_any else True) and \
                 (any(s.lower() in r.answer.lower() for s in c.expect_answer_any) if c.expect_answer_any else True)
            a_ok += ok
        if not ok:
            rep.misses.append(c.id)
    rep.top5_rate = s_ok / rep.n_search if rep.n_search else 0.0
    rep.ask_rate = a_ok / rep.n_ask if rep.n_ask else 0.0
    rep.p50_ms = statistics.median(lat) if lat else 0.0
    rep.p95_ms = _pct(lat, 0.95)
    return rep


def format_report(r: Report) -> str:
    lines = []
    if r.n_search:
        lines.append(f"search: top-5 {r.top5_rate:.0%} of {r.n_search} (target {TARGET_TOP5:.0%})   "
                     f"p50 {r.p50_ms:.0f} ms (target <{TARGET_P50_MS})   p95 {r.p95_ms:.0f} ms")
    if r.n_ask:
        lines.append(f"ask: {r.ask_rate:.0%} of {r.n_ask}")
    lines.append("misses: " + (", ".join(r.misses) if r.misses else "none"))
    return "\n".join(lines)
