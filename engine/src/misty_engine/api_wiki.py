"""Wiki-facing JSON-RPC methods: query, context, page.get, correction.file, graph.get."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .ctx import Ctx
from .errors import Fatal
from .scrub import scrub_text
from .wiki.ask import ask_wiki, query
from .wiki.context import build_context
from .wiki.graph import build_graph


def wiki_methods(ctx: Ctx) -> dict:
    async def q(p: dict) -> dict:
        return await query(ctx, str(p.get("text", "")))

    async def ask(p: dict) -> dict:
        r = await ask_wiki(ctx, str(p.get("text", "")), job_id=None)
        return {"answer": r.answer, "citations": r.citations, "filed": r.filed}

    async def context(p: dict) -> dict:
        pack = build_context(ctx, str(p.get("topic", "")), int(p.get("budget_tokens", 2000)),
                             entities=[str(x) for x in p.get("entities") or []],
                             include_private=bool(p.get("include_private", False)))
        return {"text": pack.text, "pages": pack.pages, "tokens": pack.tokens, "sources_used": pack.sources_used,
                "entities": [vars(e) for e in pack.entities], "open_now": pack.open_now, "dropped": pack.dropped}

    async def entities_lookup(p: dict) -> dict:
        from .registry.lookup import lookup
        kind = p.get("kind")
        if kind not in (None, "person", "project", "topic"):
            raise Fatal("BAD_PARAMS")
        return lookup(ctx, str(p.get("text", "")), kind, max(1, min(int(p.get("k", 5) or 5), 20)))

    async def page_get(p: dict) -> dict:
        path = str(p.get("path", ""))
        if not path.endswith(".md") or ".." in path:
            raise Fatal("BAD_PATH")
        from .registry.store import Registry
        reg = Registry(ctx.con, ctx.clock)
        target = reg.redirect(path)                        # a merged page's old path still resolves
        if target is not None:
            row = reg.get(target)
            if row is not None and row["path"]:
                path = row["path"]
        page = ctx.wiki.read(path)
        if page is None:
            raise Fatal("NOT_FOUND")
        sources = []
        for iid in page.fm.get("sources") or ([page.fm["item_id"]] if page.fm.get("item_id") else []):
            r = ctx.con.execute("SELECT id, title, locator, source FROM items WHERE id=?", (iid,)).fetchone()
            if r:
                sources.append({"item_id": r["id"], "title": r["title"], "locator": r["locator"],
                                "source": r["source"]})
        fm = json.loads(json.dumps(page.fm, default=str))
        return {"path": path, "fm": fm, "body": page.body, "sources": sources}

    async def correction(p: dict) -> dict:
        path, text = str(p.get("path", "")), " ".join(str(p.get("text", "")).split())
        if not text or len(text) > 1000 or not ctx.wiki.exists(path):
            raise Fatal("BAD_PARAMS")
        clean = scrub_text(text, "message").text           # corrections can reach a model prompt later
        ts = datetime.fromtimestamp(ctx.clock.now(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = (ctx.paths.wiki / "corrections.md").read_text()
        with ctx.wiki.batch("correction: filed"):
            ctx.wiki.write_text("corrections.md", cur.rstrip("\n") + f"\n- {ts} [[{path[:-3]}]]: {clean}\n")
        return {"ok": True}

    async def graph(p: dict) -> dict:
        return build_graph(ctx, expanded=[str(x) for x in p.get("expanded") or []],
                           focus=str(p["focus"]) if p.get("focus") else None,
                           q=str(p["q"]) if p.get("q") else None, user_label=ctx.settings.get("user_name") or "")

    async def diag_quality(_: dict) -> dict:
        from . import quality
        from pathlib import Path
        out = quality.report(ctx.paths.wiki, ctx.con)
        golden = Path(__file__).resolve().parents[3] / "tests" / "golden.yaml"      # a development file only
        if golden.is_file():
            from .evalset import load
            qs = [c.q for c in load(golden) if getattr(c, "q", None)]
            out["pack_dup_pages"] = quality.pack_duplicate_count(ctx, qs)
        return out

    async def diag_registry_dry_run(_: dict) -> dict:
        """What `registry rebuild` would do to this wiki, as counts. Rolled back, no model call, no names."""
        from .registry.rebuild import rebuild
        rep = await rebuild(ctx, dry_run=True, cache_only=True)
        return rep.counts()

    # ---- identity: the side panel's "this is the same as..." and "these are different" (spec 3.7)
    def _rerender(kept_ids: list[str]) -> None:
        from .registry import render
        from .registry.sweep import relink_settled
        for e in kept_ids:
            render.write_entity(ctx, e)
        items = [r["item_id"] for e in kept_ids for r in ctx.con.execute(
            "SELECT DISTINCT item_id FROM mentions WHERE entity_id=?", (e,))]
        relink_settled(ctx, items)
        render.refresh_links(ctx, kept_ids)

    async def entity_merge(p: dict) -> dict:
        from .registry import render
        from .registry.store import Registry, RegistryError
        reg = Registry(ctx.con, ctx.clock)
        keep, drop = str(p.get("keep", "")), str(p.get("drop", ""))
        k, d = reg.get(keep), reg.get(drop)
        if k is None or d is None or keep == drop or k["kind"] != d["kind"] or "merged" in (k["status"], d["status"]):
            raise Fatal("BAD_PARAMS")
        try:
            mid = reg.merge(keep, drop, reason="user", verdict="user")
        except RegistryError as e:
            raise Fatal(e.code)
        row = ctx.con.execute("SELECT kept, dropped FROM entity_merges WHERE id=?", (mid,)).fetchone()
        render.drop_page(ctx, row["dropped"])
        _rerender([row["kept"]])
        e = reg.get(row["kept"])
        return {"merge_id": mid, "kept": row["kept"], "path": e["path"]}

    async def entity_unmerge(p: dict) -> dict:
        from .registry.store import Registry, RegistryError
        try:
            keep, restored = Registry(ctx.con, ctx.clock).unmerge(int(p.get("merge_id", 0)))
        except (RegistryError, ValueError, TypeError) as e:
            raise Fatal(getattr(e, "code", "BAD_PARAMS"))
        _rerender([keep, restored])
        return {"keep": keep, "restored": restored}

    async def entity_rename(p: dict) -> dict:
        from .registry import render
        from .registry.store import Registry
        reg = Registry(ctx.con, ctx.clock)
        eid, name = str(p.get("id", "")), " ".join(str(p.get("name", "")).split())
        e = reg.get(eid)
        if e is None or not name or len(name) > 120 or e["status"] == "merged":
            raise Fatal("BAD_PARAMS")
        reg.rename(eid, scrub_text(name, "message").text, pinned=True)
        render.write_entity(ctx, eid)
        return {"id": eid, "path": reg.get(eid)["path"]}

    async def entity_list_merges(p: dict) -> dict:
        n = max(1, min(int(p.get("limit", 20) or 20), 100))
        rows = ctx.con.execute(
            "SELECT m.id, m.ts, m.kept, m.dropped, m.reason, m.verdict, m.undone, k.name AS kept_name, "
            "d.name AS dropped_name FROM entity_merges m JOIN entities k ON k.id = m.kept "
            "JOIN entities d ON d.id = m.dropped ORDER BY m.id DESC LIMIT ?", (n,)).fetchall()
        return {"merges": [dict(r) for r in rows]}

    # ---- privacy: keep a chat out of context packs (spec 3.10). Compiled and searchable in the app, never in a
    # pack, an ask prompt, a summary, a timeline or a graph label.
    async def chats_set_private(p: dict) -> dict:
        from .registry import privacy, render
        from .registry.store import Registry
        from .wiki import summaries
        source, cid, flag = str(p.get("source", "")), str(p.get("chat_id", "")), bool(p.get("private"))
        if not source or not cid or len(cid) > 200:
            raise Fatal("BAD_PARAMS")
        key = f"{source}:{cid}"
        cur = list(ctx.settings.get("context_private_chats") or [])
        new = sorted(set(cur) | {key}) if flag else [k for k in cur if k != key]
        ctx.settings.set("context_private_chats", new)
        reg = Registry(ctx.con, ctx.clock)
        affected = [r["entity_id"] for r in ctx.con.execute(
            "SELECT DISTINCT m.entity_id FROM mentions m JOIN items i ON i.id = m.item_id "
            "WHERE i.source=? AND json_extract(i.meta,'$.chat_id')=?", (source, cid))]
        affected = sorted({reg.survivor(e) for e in affected})
        re_summarise: list[str] = []
        for eid in affected:
            e = reg.get(eid)
            if e is None or e["status"] in ("merged", "dropped"):
                continue
            ctx.con.execute("UPDATE entity_text SET prompt_v=0 WHERE entity_id=?", (eid,))
            if e["status"] != "active":
                continue
            if render.render_entity(ctx, eid) is None:             # nothing public left: the page goes
                render.drop_page(ctx, eid)
                reg.set_status(eid, "candidate")
                reg.set_path(eid, None)
                ctx.con.execute("UPDATE entity_text SET summary='', now='[]', relationship=NULL, role=NULL, "
                                "status_label=NULL, summary_n=0 WHERE entity_id=?", (eid,))
            else:
                render.write_entity(ctx, eid)
                re_summarise.append(eid)
        if re_summarise and ctx.runner is not None:
            try:                                                   # refresh what the summaries may have carried
                await summaries.summarise_batch(ctx, re_summarise[:summaries.BATCH * 2])
            except Exception:                                      # capped or offline: it stays queued (prompt_v=0)
                pass
        render.refresh_links(ctx, re_summarise)
        ctx.extras = {**(ctx.extras or {}), "index_dirty": True}
        return {"private_chats": len(new)}

    async def chats_private_list(_: dict) -> dict:
        out = []
        for k in sorted(ctx.settings.get("context_private_chats") or []):
            source, _, cid = k.partition(":")
            out.append({"source": source, "chat_id": cid})
        return {"private": out}

    async def pr_get(_: dict) -> dict:
        from .wiki import priorities as pr
        return pr.get(ctx.wiki)

    async def pr_diff(_: dict) -> dict:
        from .wiki import priorities as pr
        cur = [pr.Line(l["id"], l["text"], l["area"]) for l in pr.get(ctx.wiki)["lines"]]
        return {"diff": [{"id": d.id, "op": d.op, "text": d.text} for d in pr.diff(cur, pr.proposed(ctx.wiki))]}

    def _sync_importance() -> None:
        """Priorities set `importance` on pages; the registry is the source of truth, so copy it across."""
        from .registry.store import Registry
        for rel in ctx.wiki.all_pages():
            if rel.split("/")[0] in ("people", "projects", "topics"):
                page = ctx.wiki.read(rel)
                eid = page.fm.get("entity_id") if page else None
                if eid and isinstance(page.fm.get("importance"), int):
                    ctx.con.execute("UPDATE entities SET importance=? WHERE id=? AND importance != ?",
                                    (page.fm["importance"], eid, page.fm["importance"]))

    async def pr_apply(p: dict) -> dict:
        from .wiki import priorities as pr
        ids = [str(x) for x in p.get("accepted_line_ids", [])]
        if pr.proposed(ctx.wiki):
            pr.apply_proposal(ctx.wiki, ctx.clock, ids)
        else:                                   # first approval of the draft: rejected lines are the ones not accepted
            cur = [l["id"] for l in pr.get(ctx.wiki)["lines"]]
            pr.apply(ctx.wiki, ctx.clock, rejected_ids=[i for i in cur if i not in ids] if ids else [])
        _sync_importance()
        return {"ok": True}

    return {"priorities.get": pr_get, "priorities.diff": pr_diff, "priorities.apply": pr_apply, "query": q, "ask": ask, "context": context, "page.get": page_get, "correction.file": correction,
            "graph.get": graph, "diag.quality": diag_quality, "diag.registry_dry_run": diag_registry_dry_run,
            "chats.set_private": chats_set_private, "chats.private_list": chats_private_list,
            "entities.lookup": entities_lookup, "entity.merge": entity_merge, "entity.unmerge": entity_unmerge, "entity.rename": entity_rename,
            "entity.list_merges": entity_list_merges}
