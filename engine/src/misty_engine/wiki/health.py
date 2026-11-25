"""Daily health check: broken links, orphans, failed compiles, stale sources, oversized about card. No LLM."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..clock import sgt_date
from ..ctx import Ctx
from ..logs import log_event
from ..pipeline import handler
from ..types import Job, approx_tokens
from .notes import links_in

STALE_S = {"gmail": 24 * 3600, "gcal": 24 * 3600}
DEFAULT_STALE_S = 7 * 24 * 3600


@dataclass
class HealthReport:
    broken_links: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    compile_failed: list[str] = field(default_factory=list)
    stale_sources: list[str] = field(default_factory=list)
    about_tokens: int = 0
    lint: dict = field(default_factory=dict)         # registry invariants, counts by name
    ok: bool = True


def check(ctx: Ctx) -> HealthReport:
    rep = HealthReport()
    pages = set(ctx.wiki.all_pages())
    inbound: set[str] = set()
    for rel in sorted(pages):
        p = ctx.wiki.read(rel)
        if p is None:
            continue
        for link in links_in(p.body):
            tgt = link + ".md"
            inbound.add(tgt)
            if tgt not in pages:
                rep.broken_links.append(f"{rel[:-3]} -> {link}")
    for rel in sorted(pages):
        if rel.split("/")[0] in ("people", "projects", "topics") and rel not in inbound:
            p = ctx.wiki.read(rel)
            if p and not p.fm.get("sources"):
                rep.orphans.append(rel[:-3])
    rep.compile_failed = [r["id"] for r in ctx.con.execute(
        "SELECT id FROM items WHERE status='compile_failed' ORDER BY id")]
    now = ctx.clock.now()
    enabled = ctx.settings.get("sources_enabled")
    for r in ctx.con.execute("SELECT source, last_ok_at FROM sources_state"):
        if not enabled.get(r["source"], False):
            continue
        limit = STALE_S.get(r["source"], DEFAULT_STALE_S)
        if r["last_ok_at"] is None or now - r["last_ok_at"] > limit:
            rep.stale_sources.append(r["source"])
    about = ctx.paths.wiki / "about-isaac.md"
    if about.exists():
        rep.about_tokens = approx_tokens(about.read_text())
    rep.ok = not (rep.broken_links or rep.compile_failed or rep.stale_sources or rep.about_tokens > 500)
    return rep


def write_report(ctx: Ctx, rep: HealthReport) -> None:
    lines = [f"# Health {sgt_date(ctx.clock.now())}", "", f"ok: {rep.ok}", "",
             f"## Broken links ({len(rep.broken_links)})"] + [f"- {x}" for x in rep.broken_links[:50]]
    lines += ["", f"## Orphans ({len(rep.orphans)})"] + [f"- {x}" for x in rep.orphans[:50]]
    lines += ["", f"## Compile failed ({len(rep.compile_failed)})"] + [f"- {x}" for x in rep.compile_failed[:50]]
    lines += ["", f"## Stale sources ({len(rep.stale_sources)})"] + [f"- {x}" for x in rep.stale_sources]
    lines += ["", "## Invariants"] + [f"- {k}: {v}" for k, v in sorted(rep.lint.items())]
    lines += ["", f"about-isaac tokens: {rep.about_tokens}"]
    ctx.wiki.write_text("health.md", "\n".join(lines) + "\n")
    ctx.settings.set("health_summary", {"ok": rep.ok, "broken_links": len(rep.broken_links),
                                         "orphans": len(rep.orphans), "compile_failed": len(rep.compile_failed),
                                         "stale_sources": rep.stale_sources, "about_tokens": rep.about_tokens,
                                         "lint": rep.lint})


@handler("health")
async def health_handler(job: Job, ctx: Ctx) -> None:
    from ..registry import render
    fixed = render.repair(ctx)                         # deterministic, no model: relink, re-render, drop orphans
    rep = check(ctx)
    rep.lint = render.lint(ctx)
    log_event("HEALTH_REPAIR", n=sum(v for k, v in fixed.items() if k != "legacy_pages"))
    with ctx.wiki.batch(f"health: {sgt_date(ctx.clock.now())}"):
        write_report(ctx, rep)
    log_event("HEALTH", ok=rep.ok, n=len(rep.broken_links) + len(rep.compile_failed))
