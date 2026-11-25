"""Job handlers (discover, prep) and the Worker that runs them.

Everything touching SQLite happens on the loop thread. Extract / scrub / chunk / embed run in a worker
thread and hand back plain values; the loop thread then writes them in one go (no awaits in between).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Awaitable, Callable, Literal, Sequence

import numpy as np

from .chunk import chunk_text
from .ctx import Ctx
from .db import tx
from .errors import ExtractError, Fatal, MistyError, SchemaUnrecognized
from .extract import MIN_LLM_WORDS
from .logs import log_event
from .scrub import Context, scrub_text
from .types import Job, Phase, RawItem, fingerprint

REGISTRY: dict[str, Callable[[Job, Ctx], Awaitable[None]]] = {}
DEDUPE_SOURCES = ("folders", "gmail")          # near-duplicates only matter where a paid compile would follow
DEDUPE_KINDS = ("file", "mail_thread")


def handler(kind: str):
    def deco(fn):
        REGISTRY[kind] = fn
        return fn
    return deco


def scrub_context(source: str) -> Context:
    if source == "gmail":
        return "mail"
    if source in ("imessage", "whatsapp", "whatsapp_export"):
        return "message"
    return "file"


def source_for(ctx: Ctx, item_source: str):
    """The Source object that can load text for items of `item_source` (repos come from the folders source)."""
    for name, src in ctx.sources.items():
        if item_source == name or item_source in getattr(src, "item_sources", ()):
            return src
    raise Fatal("NO_SOURCE")


# ---------------------------------------------------------------- items

def upsert_item(con, raw: RawItem, now: float) -> bool:
    """Insert or refresh an item. True when it needs (re)processing: new, changed, or never prepped."""
    meta = json.dumps(raw.meta, sort_keys=True)
    row = con.execute("SELECT status, meta FROM items WHERE id=?", (raw.id,)).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO items(id, source, kind, locator, title, created_at, modified_at, status, meta, updated_at) "
            "VALUES (?,?,?,?,?,?,?, 'found', ?, ?)",
            (raw.id, raw.source, raw.kind, raw.locator, raw.title, raw.created_at, raw.modified_at, meta, now))
        return True
    old = json.loads(row["meta"])
    keys = ("mtime", "size", "rev", "keep_rule")
    changed = any(old.get(k) != raw.meta.get(k) for k in keys)
    if changed:
        raw_meta = dict(raw.meta)
        if "last_rowid" in raw.meta and old.get("last_rowid") != raw.meta.get("last_rowid"):
            # chats: remember where the previous read ended so live triage looks at only the new messages
            raw_meta["prev_rowid"] = old.get("last_rowid", 0)
        if "compiled_at" in old:          # the live compile cooldown needs to know when its note was last written
            raw_meta["compiled_at"] = old["compiled_at"]
        meta = json.dumps(raw_meta, sort_keys=True)
        # status is kept: prep decides from the new fingerprint whether anything must be redone.
        # Items that were skipped or failed get another try.
        con.execute(
            "UPDATE items SET title=?, modified_at=?, meta=?, updated_at=?, "
            "status=CASE WHEN status IN ('skipped','compile_failed') THEN 'found' ELSE status END, "
            "error_code=CASE WHEN status IN ('skipped','compile_failed') THEN NULL ELSE error_code END WHERE id=?",
            (raw.title, raw.modified_at, meta, now, raw.id))
    return changed or row["status"] == "found"


def next_after_prep(con, row, phase: Phase) -> str | None:
    """Decide what happens after prep. Sets 'done' for items that never need an LLM; returns the next job kind."""
    kind, source = row["kind"], row["source"]
    no_llm = (
        row["secret_file"] or kind in ("secret_file", "image") or source == "claude_mem"
        or (kind == "file" and (row["word_count"] or 0) < MIN_LLM_WORDS)
    )
    if no_llm:
        con.execute("UPDATE items SET status='done' WHERE id=?", (row["id"],))
        return None
    return "triage" if phase == "live" else None


# ---------------------------------------------------------------- handlers

@handler("discover")
async def discover(job: Job, ctx: Ctx) -> None:
    name, limit = job.payload["source"], job.payload.get("limit")
    src = ctx.sources[name]
    now = ctx.clock.now()
    st = ctx.con.execute("SELECT cursor FROM sources_state WHERE source=?", (name,)).fetchone()
    cursor = st["cursor"] if st else None
    health, code = "ok", None
    items: list[RawItem] = []
    try:
        if hasattr(src, "check_access") and not src.check_access():
            health, code = "needs_fda", "NEEDS_FDA"
        else:
            if hasattr(src, "check_schema"):
                src.check_schema()
            items = await asyncio.to_thread(lambda: list(src.discover(cursor, limit)))
    except SchemaUnrecognized as e:
        health, code = ("needs_export" if name in ("whatsapp", "imessage") else "error"), e.code
    except Fatal as e:
        if e.code != "NEEDS_AUTH":                      # Google sign-in lost: a state to show, not a job to fail again every minute
            raise
        health, code = "needs_auth", e.code
    with tx(ctx.con):
        new = 0
        for raw in items:
            if upsert_item(ctx.con, raw, now):
                ctx.queue.enqueue("prep", item_id=raw.id)
                new += 1
        ctx.con.execute(
            "INSERT INTO sources_state(source, cursor, health, last_ok_at, error_code) VALUES (?,?,?,?,?) "
            "ON CONFLICT(source) DO UPDATE SET cursor=COALESCE(excluded.cursor, sources_state.cursor), "
            "health=excluded.health, last_ok_at=CASE WHEN excluded.health='ok' THEN excluded.last_ok_at "
            "ELSE sources_state.last_ok_at END, error_code=excluded.error_code",
            (name, src.next_cursor(), health, now, code))
    if health == "ok" and hasattr(src, "chat_names"):
        names = await asyncio.to_thread(src.chat_names)
        with tx(ctx.con):
            retitled = retitle_chat_items(ctx.con, name, names)
        if retitled:
            log_event("RETITLE", source=name, n=retitled)
    log_event("DISCOVER", source=name, count=len(items), n=new, health=health)


def retitle_chat_items(con, source: str, names: dict[str, str]) -> int:
    """Dormant chat weeks keep the title from their first discovery. Refresh them when a name resolves later."""
    n = 0
    for row in con.execute("SELECT id, title, meta FROM items WHERE source=?", (source,)).fetchall():
        meta = json.loads(row["meta"])
        name = names.get(meta.get("chat_id", ""))
        if not name or not meta.get("week"):
            continue
        title = f"{name} · {meta['week']}"
        if title != row["title"]:
            con.execute("UPDATE items SET title=? WHERE id=?", (title, row["id"]))
            n += 1
    return n


def _prep_compute(src, raw: RawItem, embedder, context: Context) -> dict:
    """Runs in a worker thread. Returns plain values only; never touches the DB."""
    body = src.load_text(raw)
    words = len(body.split())
    full = f"{raw.title}\n{body}" if body else raw.title        # the file name is searchable too
    scrubbed = scrub_text(full, context).text
    chunks = chunk_text(scrubbed)
    vectors = embedder.embed_documents(chunks)
    out = {"words": words, "fp": fingerprint(scrubbed), "chunks": chunks, "vectors": vectors, "image": None}
    if chunks:
        mean = vectors.mean(axis=0)
        n = float(np.linalg.norm(mean))
        out["item_vec"] = mean / n if n else mean
    if raw.kind == "image":
        out["image"] = embedder.embed_images([Path(raw.locator)])[0]
        out["item_vec"] = out["image"]
    return out


@handler("prep")
async def prep(job: Job, ctx: Ctx) -> None:
    row = ctx.con.execute("SELECT * FROM items WHERE id=?", (job.item_id,)).fetchone()
    if row is None:
        return
    raw = RawItem(source=row["source"], locator=row["locator"], kind=row["kind"], title=row["title"],
                  created_at=row["created_at"], modified_at=row["modified_at"], meta=json.loads(row["meta"]))
    src = source_for(ctx, row["source"])
    t0 = time.perf_counter()
    try:
        res = await asyncio.to_thread(_prep_compute, src, raw, ctx.embedder, scrub_context(row["source"]))
    except ExtractError as e:
        _skip(ctx, row["id"], e.code)
        return
    except FileNotFoundError:
        _skip(ctx, row["id"], "FILE_GONE")
        return
    except PermissionError:
        _skip(ctx, row["id"], "NO_ACCESS")
        return

    # ---- from here to the end there is no await: the DB write is atomic w.r.t. other prep jobs
    unchanged = (row["fingerprint"] == res["fp"] and row["status"] not in ("found", "skipped")
                 and ctx.con.execute("SELECT 1 FROM item_chunks WHERE item_id=? LIMIT 1", (row["id"],)).fetchone())
    if unchanged:
        return
    with tx(ctx.con):
        if res["chunks"]:
            ctx.index.replace_item_chunks(row["id"], row["source"], res["chunks"], res["vectors"])
            ctx.index.set_item_vector(row["id"], row["source"], res["item_vec"])
        if res["image"] is not None:
            ctx.index.set_image(row["id"], row["source"], res["image"])
        dup = None
        if (row["source"] in DEDUPE_SOURCES and row["kind"] in DEDUPE_KINDS
                and res["words"] >= MIN_LLM_WORDS and "item_vec" in res):
            dup = ctx.index.near_duplicate(row["id"], row["source"], res["item_vec"])
        if dup:
            # Same-format documents (past papers, templates) embed within 0.97 of each other, so a "duplicate"
            # may be a different document. Keep it searchable; just never send it to a paid compile.
            meta = json.loads(row["meta"])
            meta["dup_of"] = dup
            ctx.con.execute(
                "UPDATE items SET status='done', error_code=NULL, fingerprint=?, word_count=?, meta=?, "
                "updated_at=? WHERE id=?",
                (res["fp"], res["words"], json.dumps(meta, sort_keys=True), ctx.clock.now(), row["id"]))
            return
        ctx.con.execute(
            "UPDATE items SET status='scrubbed', error_code=NULL, fingerprint=?, word_count=?, secret_file=?, "
            "updated_at=? WHERE id=?",
            (res["fp"], res["words"], int(row["kind"] == "secret_file"), ctx.clock.now(), row["id"]))
        fresh = ctx.con.execute("SELECT * FROM items WHERE id=?", (row["id"],)).fetchone()
        nxt = next_after_prep(ctx.con, fresh, ctx.settings.get("phase"))
        if nxt:
            ctx.queue.enqueue(nxt, item_id=row["id"], cloud=(nxt == "triage"))
    log_event("PREP", item_id=row["id"], status="scrubbed", n=len(res["chunks"]),
              ms=round((time.perf_counter() - t0) * 1000))


def _skip(ctx: Ctx, item_id: str, code: str) -> None:
    ctx.con.execute("UPDATE items SET status='skipped', error_code=?, updated_at=? WHERE id=?",
                    (code, ctx.clock.now(), item_id))
    log_event("PREP_SKIP", item_id=item_id, code=code)


# ---------------------------------------------------------------- worker


class Worker:
    def __init__(self, ctx: Ctx, concurrency: dict[str, int]):
        self.ctx, self.concurrency = ctx, concurrency
        self._tasks: dict[asyncio.Task, str] = {}

    async def _run_job(self, job: Job) -> None:
        q = self.ctx.queue
        fn = REGISTRY.get(job.kind)
        try:
            if fn is None:
                raise Fatal("UNKNOWN_JOB_KIND")
            await fn(job, self.ctx)
            q.complete(job.id)
        except MistyError as e:
            q.fail(job.id, e)
        except Exception as e:      # a bug in a handler must never stop the worker or leak content
            log_event("JOB_CRASH", job_id=job.id, kind=job.kind, exc=type(e).__name__)
            q.fail(job.id, Fatal("UNEXPECTED"))

    def _fill(self, kinds: Sequence[str]) -> None:
        running = Counter(self._tasks.values())
        for kind in kinds:
            free = self.concurrency.get(kind, 1) - running[kind]
            for job in self.ctx.queue.claim([kind], free):
                t = asyncio.create_task(self._run_job(job))
                self._tasks[t] = kind
                t.add_done_callback(lambda t: self._tasks.pop(t, None))

    async def run_until_idle(self, kinds: Sequence[str] | None = None) -> None:
        kinds = list(kinds or self.concurrency)
        while True:
            self._fill(kinds)
            if not self._tasks:
                return
            await asyncio.wait(list(self._tasks), return_when=asyncio.FIRST_COMPLETED)

    async def run_forever(self, stop: asyncio.Event, kinds=None) -> None:
        """`kinds` may be a callable so the engine can change what it claims (build phase vs live phase)."""
        while not stop.is_set():
            ks = list((kinds() if callable(kinds) else kinds) or self.concurrency)
            self._fill(ks)
            if self._tasks:
                await asyncio.wait(list(self._tasks), timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass


# ---------------------------------------------------------------- reporting

def index_report(ctx: Ctx) -> dict:
    status = {r[0]: r[1] for r in ctx.con.execute("SELECT status, COUNT(*) FROM items GROUP BY status")}
    skipped = {r[0] or "?": r[1] for r in ctx.con.execute(
        "SELECT error_code, COUNT(*) FROM items WHERE status='skipped' GROUP BY error_code")}
    dupes = ctx.con.execute("SELECT COUNT(*) FROM items WHERE json_extract(meta, '$.dup_of') IS NOT NULL").fetchone()[0]
    return {"status": status, "skipped": skipped, "dupes": dupes,
            "stats": {n: dict(getattr(s, "stats", {})) for n, s in ctx.sources.items()}}


# ---------------------------------------------------------------- index command

LOCAL_SOURCES = ("folders", "claude_mem", "whatsapp_export", "gmail", "gcal")   # chats are read in M6


def keychain_has_token() -> bool:
    from . import keychain
    return keychain.get_secret("google_token") is not None


def build_sources(settings, only: str | None = None, include_images: bool = False) -> dict:
    """Source objects for the enabled local sources, built from settings."""
    import os
    from .sources.folders import FolderSource
    enabled = settings.get("sources_enabled")
    out: dict = {}
    for name in LOCAL_SOURCES:
        if only and name != only:
            continue
        if not enabled.get(name, False) and not only:
            continue
        if name == "whatsapp_export":
            paths = settings.get("whatsapp_exports")
            if paths:
                from .sources.whatsapp_export import ExportSource
                out[name] = ExportSource([Path(p) for p in paths])
            continue
        if name == "claude_mem":
            from .sources.claude_mem import ClaudeMemSource
            out[name] = ClaudeMemSource()
        if name in ("gmail", "gcal"):
            from .sources import google_auth
            if not keychain_has_token():
                continue
            if name == "gmail":
                from .sources.gmail import GmailSource
                from .paths import MistyPaths
                out[name] = GmailSource(lambda: google_auth.service("gmail"), int(settings.get("gmail_months")),
                                        allow_domains=settings.get("mail_allow_domains"),
                                        cache_path=MistyPaths.from_env().home / "mail_verdicts.json")
            else:
                from .sources.gcal import CalendarSource
                out[name] = CalendarSource(lambda: google_auth.service("calendar"),
                                           tuple(settings.get("gcal_calendars")))
        if name == "folders":
            roots = [Path(os.path.expanduser(p)) for p in settings.get("folders")]
            excl = [Path(os.path.expanduser(p)) for p in settings.get("excluded_paths")]
            out[name] = FolderSource(roots, excl, include_images)
    return out


def build_live_sources(settings) -> dict:
    """Everything the engine watches while running: local sources, Gmail/Calendar, and (with FDA) the chats."""
    out = build_sources(settings)
    enabled = settings.get("sources_enabled")
    if enabled.get("imessage"):
        from .sources.contacts import ContactsResolver
        from .sources.imessage import IMessageSource
        try:
            contacts = ContactsResolver()
        except Exception:
            contacts = None
        out["imessage"] = IMessageSource(contacts=contacts, excluded=set(settings.get("imessage_excluded")))
    if enabled.get("whatsapp"):
        from .sources.whatsapp import WhatsAppSource
        out["whatsapp"] = WhatsAppSource(picked=set(settings.get("whatsapp_picked")))
    return out


async def run_index(ctx: Ctx, source: str | None = None, limit: int | None = None,
                    include_images: bool = False) -> dict:
    t0 = time.perf_counter()
    ctx.sources = build_sources(ctx.settings, source, include_images)
    if source and source not in ctx.sources:
        raise Fatal("UNKNOWN_SOURCE")
    for name in ctx.sources:
        ctx.queue.enqueue("discover", payload={"source": name, "limit": limit})
    await Worker(ctx, {"discover": 1, "prep": 4}).run_until_idle(["discover", "prep"])
    rep = index_report(ctx)
    rep["wall_s"] = round(time.perf_counter() - t0, 1)
    return rep


def format_report(rep: dict) -> str:
    lines = [f"{'status':<16}{'items':>8}"]
    for k in ("found", "scrubbed", "done", "skipped", "compile_failed"):
        lines.append(f"{k:<16}{rep['status'].get(k, 0):>8}")
    if rep["skipped"]:
        lines.append("skipped by reason: " + ", ".join(f"{k}={v}" for k, v in sorted(rep["skipped"].items())))
    if rep.get("dupes"):
        lines.append(f"near-duplicates (searchable, not compiled): {rep['dupes']}")
    for name, st in rep["stats"].items():
        if st:
            lines.append(f"{name}: " + ", ".join(f"{k}={v}" for k, v in sorted(st.items())))
    lines.append(f"wall time: {rep['wall_s']} s")
    return "\n".join(lines)


from . import cloud as _cloud  # noqa: E402,F401  (registers worth / compile_batch / embed_note)
from .registry import sweep as _resolve  # noqa: E402,F401  (registers resolve)
from .wiki import summaries as _summaries  # noqa: E402,F401  (registers summarise)
from .wiki import synth as _synth  # noqa: E402,F401  (registers synth steps)
from . import live as _live  # noqa: E402,F401  (registers triage)
from .wiki import health as _health, priorities as _prio  # noqa: E402,F401
from . import watch as _watch  # noqa: E402,F401  (registers discover_paths)
from . import prep as _prep  # noqa: E402,F401  (registers prep_cards)
from .teach import build as _teach_build  # noqa: E402,F401  (registers teach_map)
from .teach import plan as _teach_plan  # noqa: E402,F401  (registers teach_plan)
from .teach import bank as _teach_bank  # noqa: E402,F401  (registers teach_bank)
from .teach import slips as _teach_slips  # noqa: E402,F401  (registers teach_slip_label)
from .teach import paper as _teach_paper  # noqa: E402,F401  (registers teach_paper_parse)
