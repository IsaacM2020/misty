"""Live triage: a new item arrives, Jev judges ONLY the new content, plain code decides what happens next."""
from __future__ import annotations

import asyncio
import json
import re

from . import suggest
from .ctx import Ctx
from .db import tx
from .errors import Fatal
from .logs import log_event
from .pipeline import handler, source_for
from .scrub import scrub_text
from .triage import decide_triage
from .types import Job, RawItem

MSG_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2}\] From:", re.M)


def newest_mail_message(text: str) -> str:
    """Subject line + the last message of a thread (the scrubbed text from prep)."""
    starts = [m.start() for m in MSG_RE.finditer(text)]
    if not starts:
        return text
    subject = text.split("\n", 1)[0] if text.startswith("Subject:") else ""
    return (subject + "\n" if subject else "") + text[starts[-1]:]


def stale_chat_week(kind: str, meta: dict, now: float) -> bool:
    """A chat week older than last week is history from the backfill, not something to act on now."""
    from .sources.imessage import iso_week
    wk = meta.get("week")
    return kind == "chat_week" and bool(wk) and wk not in (iso_week(now), iso_week(now - 7 * 86400))


def areas_in_wiki(ctx: Ctx) -> list[str]:
    return [p[len("areas/"):-3] for p in ctx.wiki.all_pages() if p.startswith("areas/")]


async def _excerpt(ctx: Ctx, row) -> str:
    from .cloud import item_text
    kind, meta = row["kind"], json.loads(row["meta"])
    if kind == "chat_week":
        src = source_for(ctx, row["source"])
        raw = RawItem(source=row["source"], locator=row["locator"], kind=kind, title=row["title"], meta=meta)
        text = await asyncio.to_thread(src.new_messages_text, raw, int(meta.get("prev_rowid") or 0))
        return scrub_text(text, "message").text
    text = item_text(ctx, row["id"])
    if kind in ("mail_thread", "mail_promo"):
        return newest_mail_message(text)
    return text


@handler("triage")
async def triage(job: Job, ctx: Ctx) -> None:
    row = ctx.con.execute("SELECT * FROM items WHERE id=?", (job.item_id,)).fetchone()
    if row is None:
        return
    excerpt = await _excerpt(ctx, row)
    if not excerpt.strip():
        ctx.con.execute("UPDATE items SET status='done' WHERE id=?", (row["id"],))
        return
    ans = await ctx.jev.triage(excerpt, areas_in_wiki(ctx), job_id=job.id, phase="live")
    d = decide_triage(ans)
    meta = json.loads(row["meta"])
    now = ctx.clock.now()
    with tx(ctx.con):
        nxt = "done"
        if d.note and row["kind"] != "mail_promo":
            nxt = "triaged"
        # a wanted card waits for the note: its text is the `do` line Haiku writes in the compile sweep
        meta.pop("pending_card", None)
        if (nxt == "triaged" and d.suggestion_kind and not stale_chat_week(row["kind"], meta, now)
                and suggest.card_allowed(row["kind"], meta, excerpt)):
            meta["pending_card"] = {"kind": d.suggestion_kind, "urgency": d.urgency}
        ctx.con.execute("UPDATE items SET matters=?, area=?, status=?, meta=?, updated_at=? WHERE id=?",
                        (int(d.matters), d.area, nxt, json.dumps(meta, sort_keys=True), now, row["id"]))
        # kept items stay 'triaged' until the scheduler's Haiku sweep batches them (live_compile, P37)
    log_event("TRIAGE", item_id=row["id"], keep=d.note, status=nxt)
