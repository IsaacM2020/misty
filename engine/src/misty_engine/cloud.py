"""Cloud handlers: Jev `worth` gate, Haiku `compile_batch`, and the local `embed_note` that follows.

Every cloud call goes through ctx.openrouter / ctx.jev (the single client). Items arrive here already scrubbed.
"""
from __future__ import annotations

import asyncio
import json

from .chunk import join_chunks
from .clock import sgt_date
from .compile.batching import make_batches
from .compile.haiku import build_messages, parse_response
from .compile.prompts import BatchItem, render_batch
from .ctx import Ctx
from .db import tx
from .logs import log_event
from .pipeline import REGISTRY, handler
from .triage import decide_worth
from .types import Job
from .wiki.notes import render_source_note, section, source_note_path

ALWAYS_COMPILE = ("allowlist", "starred")        # mail kept by an explicit user rule skips the worth gate (P34)
CLOUD_KINDS = ("file", "repo", "mail_thread", "calendar_week", "chat_week")   # P37: chats too (ZDR); promos never


def item_text(ctx: Ctx, item_id: str) -> str:
    rows = ctx.con.execute("SELECT text_scrubbed FROM item_chunks WHERE item_id=? ORDER BY chunk_no", (item_id,))
    return join_chunks([r[0] for r in rows])


def item_tokens(ctx: Ctx, item_id: str) -> int:
    r = ctx.con.execute("SELECT COALESCE(SUM(LENGTH(text_scrubbed)),0) FROM item_chunks WHERE item_id=?",
                        (item_id,)).fetchone()
    return int(r[0]) // 4


def _phase(ctx: Ctx):
    return ctx.settings.get("phase")


# ---------------------------------------------------------------- worth

@handler("worth")
async def worth(job: Job, ctx: Ctx) -> None:
    row = ctx.con.execute("SELECT * FROM items WHERE id=?", (job.item_id,)).fetchone()
    if row is None or row["status"] != "scrubbed":
        return
    if json.loads(row["meta"] or "{}").get("keep_rule") in ALWAYS_COMPILE:
        ctx.con.execute("UPDATE items SET status='triaged', matters=1, updated_at=? WHERE id=?",
                        (ctx.clock.now(), row["id"]))
        log_event("WORTH", item_id=row["id"], keep=True, conf=1.0)
        return
    text = item_text(ctx, row["id"])
    ans = await ctx.jev.worth(text, job_id=job.id, phase=_phase(ctx), build_id=job.payload.get("build_id"))
    keep = decide_worth(ans)
    ctx.con.execute("UPDATE items SET status=?, matters=?, updated_at=? WHERE id=?",
                    ("triaged" if keep else "done", int(keep), ctx.clock.now(), row["id"]))
    log_event("WORTH", item_id=row["id"], keep=keep, conf=round(ans.confidence, 2))


# ---------------------------------------------------------------- compile

def store_notes(ctx: Ctx, by_id: dict, drafts: dict) -> list[str]:
    """Write source notes for validated drafts (one wiki commit), update the items, queue embed_note."""
    if not drafts:
        return []
    today = sgt_date(ctx.clock.now())
    now = ctx.clock.now()
    paths: dict[str, str] = {}
    with ctx.wiki.batch(f"compile: {len(drafts)} source notes"):
        for iid, draft in drafts.items():
            r = by_id[iid]
            fm, body = render_source_note(draft, item_id=iid, source=r["source"], today=today)
            # a recompile keeps the item's file: a new title would orphan the old note and its timeline links
            path = r["note_path"] or source_note_path(r["source"], iid, draft.title)
            ctx.wiki.write(path, fm, body)
            paths[iid] = path
    with tx(ctx.con):
        for iid, draft in drafts.items():
            meta = json.loads(by_id[iid]["meta"])
            meta.pop("pending_card", None)
            meta.update({"dated_lines": [list(d) for d in draft.dates], "people": list(draft.people),
                         "projects": list(draft.projects), "topics": list(draft.topics),
                         "area_hint": draft.area_hint, "compiled_at": now})
            ctx.con.execute(
                "UPDATE items SET status='compiled', note_path=?, matters=1, meta=?, error_code=NULL, "
                "updated_at=? WHERE id=?", (paths[iid], json.dumps(meta, sort_keys=True), now, iid))
            ctx.queue.enqueue("embed_note", item_id=iid)
    return list(paths.values())


def _chat_labels(row) -> tuple[str, str]:
    """(with_name, group): who a 1:1 chat is with, or a group chat's title, for the item tag."""
    if row["kind"] != "chat_week":
        return "", ""
    meta = json.loads(row["meta"] or "{}")
    if meta.get("chat_kind") == "group":
        return "", (row["title"] or "").split(" · ")[0].strip()
    cp = meta.get("counterpart")
    name = cp.get("name") if isinstance(cp, dict) else None
    return (name or ""), ""


@handler("compile_batch")
async def compile_batch(job: Job, ctx: Ctx) -> None:
    ids: list[str] = job.payload["item_ids"]
    retry = bool(job.payload.get("retry"))
    marks = ",".join("?" * len(ids))
    rows = ctx.con.execute(
        f"SELECT * FROM items WHERE id IN ({marks}) AND status IN ('triaged','scrubbed')", ids).fetchall()
    rows = [r for r in rows if r["kind"] in CLOUD_KINDS]          # promos never go to the cloud
    if not rows:
        return
    live = [r["id"] for r in rows]
    texts = {r["id"]: item_text(ctx, r["id"]) for r in rows}
    batch = [BatchItem(r["id"], r["source"], r["kind"], r["title"], texts[r["id"]], *_chat_labels(r)) for r in rows]
    from .registry.hints import known_block
    known = known_block(ctx, rows, texts=texts)       # the spellings the wiki already uses, in the user message
    res = await ctx.openrouter.chat(
        model_key=(ctx.settings.get("job_models") or {}).get("compile", "haiku"),
        messages=build_messages(render_batch(batch, sgt_date(ctx.clock.now()), known)),
        max_tokens=3500 if retry else min(8000, 800 * len(batch) + 600), job_id=job.id, phase=_phase(ctx),
        build_id=job.payload.get("build_id"), purpose="compile", json_out=True,
        accept=lambda text: not parse_response(text, live).bad)     # a local reply with any bad note goes to the cloud instead
    parsed = parse_response(res.text, live)
    if res.finish_reason == "length":              # cut off at the output cap: say so instead of "bad JSON"
        parsed.bad = {i: "TRUNCATED" for i in parsed.bad}
    elif res.finish_reason == "content_filter":    # the provider refused this text: retrying will not help
        parsed.bad = {i: "CONTENT_FILTER" for i in parsed.bad}
    by_id = {r["id"]: r for r in rows}
    store_notes(ctx, by_id, parsed.ok)
    cards = cards_from_drafts(ctx, by_id, parsed.ok)
    now = ctx.clock.now()
    with tx(ctx.con):
        for iid, code in parsed.bad.items():
            if retry:
                ctx.con.execute("UPDATE items SET status='compile_failed', error_code=?, updated_at=? WHERE id=?",
                                (code, now, iid))
            else:
                ctx.queue.enqueue("compile_batch", payload={"item_ids": [iid], "retry": True,
                                                            "build_id": job.payload.get("build_id")}, cloud=True)
    details = res.usage.get("completion_tokens_details") or {}
    log_event("COMPILE", job_id=job.id, n=len(parsed.ok), bad=len(parsed.bad), usd=res.cost_usd,
              cached=(res.usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
              tokens_out=res.usage.get("completion_tokens", 0), reasoning=details.get("reasoning_tokens", 0),
              finish=res.finish_reason or "none")
    if ctx.notify:
        for c in cards:
            await ctx.notify("suggestion.new", c)


def cards_from_drafts(ctx: Ctx, by_id: dict, drafts: dict) -> list[dict]:
    """Live triage asked for a card (meta.pending_card); Haiku's `do` line is its text. Empty `do` = no card."""
    from . import suggest
    out = []
    with tx(ctx.con):
        for iid, draft in drafts.items():
            meta = json.loads(by_id[iid]["meta"])
            want = meta.get("pending_card")
            if not want or not draft.do:
                continue
            sender = meta.get("sender") or suggest.chat_handle(meta) or None
            sid = suggest.place_card(ctx.con, iid, want["kind"], draft.do, want["urgency"], sender, ctx.clock)
            if sid:
                out.append({"id": sid, "kind": want["kind"], "urgency": want["urgency"]})
    return out


# ---------------------------------------------------------------- embed the compiled note

@handler("embed_note")
async def embed_note(job: Job, ctx: Ctx) -> None:
    row = ctx.con.execute("SELECT * FROM items WHERE id=?", (job.item_id,)).fetchone()
    if row is None or not row["note_path"]:
        return
    page = ctx.wiki.read(row["note_path"])
    if page is None:
        return
    text = "\n".join(x for x in (page.fm.get("title", ""), section(page.body, "Summary"),
                                 section(page.body, "Key facts")) if x)
    vec = (await asyncio.to_thread(ctx.embedder.embed_documents, [text]))[0]
    ctx.index.upsert_page(row["note_path"], text, vec)
    ctx.con.execute("UPDATE items SET status='embedded', updated_at=? WHERE id=?", (ctx.clock.now(), row["id"]))
    log_event("EMBED_NOTE", item_id=row["id"])
