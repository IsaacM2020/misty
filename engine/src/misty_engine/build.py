"""The first-build orchestrator: discover -> prep -> worth (Jev) -> compile (Haiku) -> embed notes (-> link, M4)."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable

from .cloud import CLOUD_KINDS
from .compile import prompts
from .compile.batching import make_batches
from .ctx import Ctx
from .errors import Fatal
from .logs import log_event
from .pipeline import Worker, build_sources
from .types import approx_tokens, new_id

# job kinds that belong to a build, in the order work flows through them (M4 appends `link`, synthesis steps)
JEV_QUESTION_TOKENS = 100
HAIKU_OUT_TOKENS = 550
MAX_ITEM_TOKENS = 8000


@dataclass
class Estimate:
    items: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    jev_usd: float = 0.0
    haiku_usd: float = 0.0
    total_usd: float = 0.0
    by_source: dict = field(default_factory=dict)
    cap_usd: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def eligible_rows(ctx: Ctx, statuses=("scrubbed",), ids: set[str] | None = None) -> list[tuple[str, str, int]]:
    marks = ",".join("?" * len(CLOUD_KINDS))
    smarks = ",".join("?" * len(statuses))
    rows = ctx.con.execute(
        f"SELECT i.id, i.source, COALESCE(SUM(LENGTH(c.text_scrubbed)),0)/4 AS tok FROM items i "
        f"LEFT JOIN item_chunks c ON c.item_id=i.id WHERE i.status IN ({smarks}) AND i.kind IN ({marks}) "
        f"GROUP BY i.id ORDER BY i.id", (*statuses, *CLOUD_KINDS)).fetchall()
    out = [(r["id"], r["source"], int(r["tok"])) for r in rows]
    if ids is not None:
        out = [r for r in out if r[0] in ids]
    return out


def pick_sample(rows: list[tuple[str, str, int]], n: int) -> list[str]:
    """Deterministic stratified sample: proportional per source, within a source ordered by sha256(item_id)."""
    if n >= len(rows):
        return [r[0] for r in rows]
    by_src: dict[str, list[str]] = defaultdict(list)
    for iid, src, _ in rows:
        by_src[src].append(iid)
    total = len(rows)
    quotas = {s: n * len(v) / total for s, v in by_src.items()}
    take = {s: int(q) for s, q in quotas.items()}
    # every source with items gets at least one slot when there is room, then largest remainder fills the rest
    for s in sorted(by_src):
        if take[s] == 0 and sum(take.values()) < n:
            take[s] = 1
    rest = n - sum(take.values())
    for s in sorted(by_src, key=lambda s: (quotas[s] - int(quotas[s]), s), reverse=True):
        if rest <= 0:
            break
        if take[s] < len(by_src[s]):
            take[s] += 1
            rest -= 1
    picked: list[str] = []
    for s, ids in by_src.items():
        ordered = sorted(ids, key=lambda i: hashlib.sha256(i.encode()).hexdigest())
        picked += ordered[:take[s]]
    return sorted(picked)


def estimate(ctx: Ctx, limit: int | None = None, *, extra_mail_threads: int = 0,
             mail_avg_tokens: float = 0.0) -> Estimate:
    p = ctx.pinned.prices
    rows = eligible_rows(ctx, ("scrubbed", "triaged"))
    if limit:
        rows = [r for r in rows if r[0] in set(pick_sample(rows, limit))]
    items = [(i, s, t) for i, s, t in rows]
    if extra_mail_threads and mail_avg_tokens and not limit:
        items += [(f"future_{k}", "gmail", int(mail_avg_tokens)) for k in range(extra_mail_threads)]
    card = approx_tokens(ctx.card())
    est = Estimate(cap_usd=float(ctx.settings.get("cap_build_usd")))
    jev_in = haiku_in = 0
    by_src: dict[str, dict] = defaultdict(lambda: {"items": 0, "tokens": 0})
    for _, src, tok in items:
        jev_in += min(tok, ctx.pinned.jev.max_input_tokens) + card + JEV_QUESTION_TOKENS
        haiku_in += min(tok, MAX_ITEM_TOKENS) + 30
        by_src[src]["items"] += 1
        by_src[src]["tokens"] += tok
    batches = make_batches([(i, min(t, MAX_ITEM_TOKENS)) for i, _, t in items])
    sys_tok = approx_tokens(prompts.SYSTEM)
    est.items = len(items)
    est.in_tokens = jev_in + haiku_in
    est.out_tokens = len(items) * HAIKU_OUT_TOKENS
    est.jev_usd = jev_in * p.jev_in / 1e6
    est.haiku_usd = (haiku_in * p.haiku_in + est.out_tokens * p.haiku_out
                     + len(batches) * sys_tok * p.haiku_cache_read + sys_tok * p.haiku_cache_write) / 1e6
    est.total_usd = round(est.jev_usd + est.haiku_usd, 4)
    est.jev_usd, est.haiku_usd = round(est.jev_usd, 4), round(est.haiku_usd, 4)
    est.by_source = dict(by_src)
    return est


def format_estimate(e: Estimate) -> str:
    lines = [f"items to send through Jev + Haiku: {e.items}",
             f"tokens in: {e.in_tokens:,}   tokens out (est): {e.out_tokens:,}",
             f"Jev ${e.jev_usd:.4f}   Haiku ${e.haiku_usd:.4f}   TOTAL ${e.total_usd:.4f}   (cap ${e.cap_usd:.2f})"]
    for s, v in sorted(e.by_source.items()):
        lines.append(f"  {s}: {v['items']} items, {v['tokens']:,} tokens")
    if e.total_usd > e.cap_usd:
        lines.append(f"!! estimate is OVER the cap of ${e.cap_usd:.2f}")
    return "\n".join(lines)


class Build:
    def __init__(self, ctx: Ctx, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 sources: dict | None = None):
        self.ctx, self.sleep, self._sources = ctx, sleep, sources
        self.build_id: str | None = None

    # ---- bookkeeping
    def _row(self, include_done: bool = True):
        if self.build_id:
            return self.ctx.con.execute("SELECT * FROM builds WHERE id=?", (self.build_id,)).fetchone()
        where = "" if include_done else "WHERE status IN ('running','paused') "
        return self.ctx.con.execute(
            f"SELECT * FROM builds {where}ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()

    def _phase_set(self, phase: str) -> None:
        self.ctx.con.execute("UPDATE builds SET phase=? WHERE id=?", (phase, self.build_id))
        log_event("BUILD_PHASE", phase=phase)

    def start(self, limit: int | None = None) -> str:
        c = self.ctx
        self.build_id = new_id("bd")
        c.con.execute("INSERT INTO builds(id, started_at, item_limit, phase, status) VALUES (?,?,?,?, 'running')",
                      (self.build_id, c.clock.now(), limit, "discover"))
        c.settings.set("phase", "build")
        c.settings.set("build_sample", None)
        return self.build_id

    def _kinds_pending(self, status: str, kinds: tuple[str, ...]) -> int:
        marks = ",".join("?" * len(kinds))
        return self.ctx.con.execute(
            f"SELECT COUNT(*) FROM jobs WHERE status=? AND kind IN ({marks})", (status, *kinds)).fetchone()[0]

    BUILD_KINDS = ("discover", "prep", "worth", "compile_batch", "embed_note", "resolve", "summarise")
    # phase order of a build (spec 3.7); `areas` waits for the person's accept step, as before
    PHASES = ("discover", "prep", "worth", "compile", "resolve", "summarise", "areas", "about", "priorities")

    def status(self, include_done: bool = True) -> dict | None:
        row = self._row(include_done)
        if row is None:
            return None
        c = self.ctx
        sample = c.settings.get("build_sample")
        marks = ",".join("?" * len(CLOUD_KINDS))
        args: list = list(CLOUD_KINDS)
        sql = (f"SELECT status, matters, COUNT(*) n FROM items WHERE kind IN ({marks}) "
               f"AND status IN ('scrubbed','triaged','compiled','embedded','linked','compile_failed','done') ")
        rows = c.con.execute(sql + "GROUP BY status, matters", args).fetchall()
        if sample is not None:
            ids = set(sample)
            rows = c.con.execute(
                "SELECT status, matters, 1 n FROM items WHERE id IN (%s)" % ",".join("?" * len(ids)),
                list(ids)).fetchall() if ids else []
        total = done = failed = 0
        for r in rows:
            st, matters, n = r["status"], r["matters"], r["n"]
            if st == "done" and matters is None:
                continue                                   # finished before the build (tiny files, dups)
            total += n
            if st == "compile_failed":
                failed += n
            elif st in ("compiled", "embedded", "linked") or (st == "done" and matters is not None):
                done += n
        paused_by = ("cap" if self._kinds_pending("waiting_cap", self.BUILD_KINDS)
                     else "network" if self._kinds_pending("waiting_network", self.BUILD_KINDS) else None)
        return {"id": row["id"], "phase": row["phase"], "done": done, "total": total, "failed": failed,
                "spent_usd": round(c.spend.spent_build(row["id"]), 6), "paused_by": paused_by}

    # ---- running
    async def _drain(self, kinds: tuple[str, ...], conc: dict[str, int]) -> None:
        c = self.ctx
        worker = Worker(c, conc)
        marks = ",".join("?" * len(kinds))
        while True:
            await worker.run_until_idle(list(kinds))
            if c.settings.get("paused"):
                return
            nxt = c.con.execute(
                f"SELECT MIN(next_run_at) FROM jobs WHERE status='queued' AND kind IN ({marks})", kinds).fetchone()[0]
            if nxt is None:
                return
            await self.sleep(max(0.0, nxt - c.clock.now()))

    async def run(self, limit: int | None = None, *, resume: bool = False) -> dict:
        c = self.ctx
        if not resume or self.build_id is None:
            self.start(limit)
        row = self._row()
        limit = row["item_limit"]
        # 1. discover + prep (local, free)
        c.sources = self._sources if self._sources is not None else build_sources(c.settings)
        self._phase_set("discover")
        for name in c.sources:
            c.queue.enqueue("discover", payload={"source": name})
        await self._drain(("discover", "prep"), {"discover": 1, "prep": 4})
        # 2. worth: Jev, cheap
        self._phase_set("worth")
        todo = eligible_rows(c)
        sample = c.settings.get("build_sample")
        if sample is None:
            sample = pick_sample(todo, limit) if limit else None
            c.settings.set("build_sample", sample)
        want = set(sample) if sample is not None else None
        for iid, _, _ in eligible_rows(c, ("scrubbed",), want):
            c.queue.enqueue("worth", item_id=iid, payload={"build_id": self.build_id}, cloud=True)
        await self._drain(("worth",), {"worth": 16})
        # 3. compile: Haiku, batched. First request alone so the prompt cache is written before the fan-out.
        self._phase_set("compile")
        triaged = eligible_rows(c, ("triaged",), want)
        batches = make_batches([(i, min(t, MAX_ITEM_TOKENS)) for i, _, t in triaged])
        if self._cache_cold() and batches:
            first, batches = batches[0], batches[1:]
            c.queue.enqueue("compile_batch", payload={"item_ids": first, "build_id": self.build_id}, cloud=True)
            await self._drain(("compile_batch", "embed_note"), {"compile_batch": 1, "embed_note": 1})
        for ids in batches:
            c.queue.enqueue("compile_batch", payload={"item_ids": ids, "build_id": self.build_id}, cloud=True)
        self._phase_set("embed")
        await self._drain(self._follow_kinds(), {"compile_batch": 50, "embed_note": 1})
        # 4. resolve: who is each mention? One sweep over everything compiled (spec 3.2). A judge that is capped or
        # offline parks the clusters that need it as pending; the build stays paused and the next run resumes.
        self._phase_set("resolve")
        from .errors import CapHit, NetworkDown
        from .registry.sweep import resolve_sweep
        blocked = None
        try:
            await resolve_sweep(c, build_id=self.build_id)
        except (CapHit, NetworkDown) as e:
            blocked = "cap" if isinstance(e, CapHit) else "network"
        # 5. summarise: every entity that qualifies gets its summary, and only then its page (8 calls at a time)
        if blocked is None and c.runner is not None:
            self._phase_set("summarise")
            from .wiki.summaries import summarise_stale
            try:
                await summarise_stale(c, build_id=self.build_id)
            except (CapHit, NetworkDown) as e:
                blocked = "cap" if isinstance(e, CapHit) else "network"
        # 6-8. areas (one strong-model call, only when none are accepted; the person accepts), then the about card
        # and the first priorities draft. All through the API runner; without a runner (tests) they are skipped.
        waiting_areas = False
        if blocked is None and c.runner is not None:
            from .errors import Fatal
            from .wiki import areas as areasmod
            from .wiki import synth
            try:
                self._phase_set("areas")
                if not areasmod.accepted(c):
                    if not areasmod.read_areas(c):
                        await areasmod.discover(c)
                    waiting_areas = True
                else:
                    self._phase_set("about")
                    await synth.about_card(c)
                    c.settings.set("synth_done", True)
                    self._phase_set("priorities")
                    await synth.draft_priorities(c)
            except Fatal as e:                      # too few notes for areas, or the model gave nothing usable
                log_event("BUILD_CORE_WAIT", code=e.code)
                waiting_areas = True
            except (CapHit, NetworkDown) as e:
                blocked = "cap" if isinstance(e, CapHit) else "network"
        from .wiki.index_md import write_index
        write_index(c.wiki)
        st = self.status()
        if waiting_areas:
            st["paused_by"] = st["paused_by"] or "areas"
        finished = (st["paused_by"] is None and blocked is None and not waiting_areas
                    and not c.queue.pending(("worth", "compile_batch")))
        if finished:
            self._phase_set("done")
            c.con.execute("UPDATE builds SET status='done', finished_at=? WHERE id=?", (c.clock.now(), self.build_id))
            if c.settings.get("synth_done"):
                c.settings.set("phase", "live")    # a rebuild on top of a finished wiki: back to keeping current
        else:
            c.con.execute("UPDATE builds SET status='paused' WHERE id=?", (self.build_id,))
        out = self.status()
        if waiting_areas and out is not None:
            out["paused_by"] = out["paused_by"] or "areas"
        return out

    def _cache_cold(self) -> bool:
        return True

    def _follow_kinds(self) -> tuple[str, ...]:
        from .pipeline import REGISTRY
        return tuple(k for k in ("compile_batch", "embed_note") if k in REGISTRY)


