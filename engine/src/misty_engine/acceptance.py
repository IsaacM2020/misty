"""The acceptance command (spec section 5): A1 to A7 measured on the real wiki, counts only.

A6 (a cheaper answering model loses little) spends a little and runs only with `with_answers=True`. Everything else is
free and local. A row that fails means Part 1 is not done."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .ctx import Ctx


@dataclass
class Row:
    id: str
    name: str
    value: str
    target: str
    ok: bool


def spend_by_purpose(ctx: Ctx, since_ts: float = 0.0) -> dict:
    """USD per purpose since a time, plus per 1,000 compiled items. Purposes are codes, never content."""
    rows = ctx.con.execute("SELECT COALESCE(purpose,'(none)') AS p, SUM(usd) AS usd, COUNT(*) AS n FROM spend "
                           "WHERE ts >= ? GROUP BY p ORDER BY usd DESC", (since_ts,)).fetchall()
    compiled = ctx.con.execute("SELECT COUNT(*) FROM items WHERE note_path IS NOT NULL AND updated_at >= ?",
                               (since_ts,)).fetchone()[0]
    total = sum(r["usd"] for r in rows)
    per_k = {r["p"]: (r["usd"] / compiled * 1000 if compiled else None) for r in rows}
    return {"since": since_ts, "total_usd": round(total, 6), "compiled_items": compiled,
            "by_purpose": {r["p"]: {"usd": round(r["usd"], 6), "calls": r["n"]} for r in rows},
            "per_1000_items": {k: (round(v, 4) if v is not None else None) for k, v in per_k.items()}}


def _no_subscription_paths(src: Path) -> int:
    c = "clau" + "de"                                         # spelled in pieces: this file must not match itself
    pat = re.compile("|".join([c + "_runner", "Clau" + "deRunner", c + "_cli", "Quota" + "Hit", c + " -p"]))
    n = 0
    for p in src.rglob("*.py"):
        n += len(pat.findall(p.read_text()))
    return n


async def run(ctx: Ctx, golden_path: Path | None = None, dups_path: Path | None = None, *,
              with_answers: bool = False) -> list[Row]:
    from . import evaljudge
    from .evalset import load
    from .registry import render
    from .registry.calibrate import measure
    from .wiki.graph import build_graph
    rows: list[Row] = []
    # A1: no duplicate on the golden groups, no apart pair merged
    if dups_path and Path(dups_path).is_file():
        dups = yaml.safe_load(Path(dups_path).read_text()) or {}
        pct, neg = measure(ctx, dups)
        rows.append(Row("A1", "golden duplicate groups complete (or one judge question away)", f"{pct:.0f}%", "100%",
                        pct >= 100.0))
        rows.append(Row("A1", "golden apart pairs merged", str(neg), "0", neg == 0))
    # A2: lint
    lint = render.lint(ctx)
    for key in ("unsummarised_2plus", "dead_links", "chat_name_people"):
        rows.append(Row("A2", key, str(lint[key]), "0", lint[key] == 0))
    # A3: retrieval and packs
    cases = load(Path(golden_path)) if golden_path and Path(golden_path).is_file() else []
    rep = evaljudge.EvalReport()
    if cases:
        evaljudge.retrieval_and_pack(ctx, cases, rep)
        rows.append(Row("A3", "golden retrieval top-5", f"{rep.top5:.0%}", ">=90%", rep.top5 >= 0.90))
        rows.append(Row("A3", "packs with two entries for one entity", f"{rep.pack_dup_rate:.0%}", "0%",
                        rep.pack_dup_rate == 0))
    # A4: the graph
    g = build_graph(ctx)
    kinds = {e["kind"] for e in g["edges"]}
    rows.append(Row("A4", "default graph nodes", str(len(g["nodes"])), "<=60", len(g["nodes"]) <= 60))
    rows.append(Row("A4", "edge kinds", ",".join(sorted(kinds)) or "none", "belongs|involves|related",
                    kinds <= {"belongs", "involves", "related"}))
    # A5: no subscription path, sub-caps in force
    src = Path(__file__).resolve().parent
    n = _no_subscription_paths(src)
    caps = ctx.settings.get("purpose_caps_daily") or {}
    rows.append(Row("A5", "subscription code paths in engine/src", str(n), "0", n == 0))
    rows.append(Row("A5", "per-purpose daily sub-caps set", ",".join(sorted(caps)), "judge,summarise,ask,core",
                    {"judge", "summarise", "ask", "core"} <= set(caps)))
    # A6: the model swap (spends)
    if with_answers and cases:
        full = await evaljudge.run_suites(ctx, cases, ("answers",), cheap=True)
        rows.append(Row("A6", "loss going to the cheap model", f"{full.loss:.0%}", "<=10%",
                        full.loss <= 0.10 and not full.incomplete))
        rows.append(Row("A6", "lift of the wiki's packs over search alone", f"{full.lift:.2f}", ">=0.3",
                        full.lift >= 0.3 and not full.incomplete))
    # A7: privacy
    from . import quality
    leaks = quality.report(ctx.paths.wiki, ctx.con)["private_leaks"]
    rows.append(Row("A7", "pages citing a private chat", str(leaks), "0", leaks == 0))
    return rows


def format_rows(rows: list[Row]) -> str:
    w = max(len(r.name) for r in rows) if rows else 10
    return "\n".join(f"{r.id}  {'ok  ' if r.ok else 'FAIL'}  {r.name:<{w}}  {r.value:>8}  (target {r.target})"
                     for r in rows)
