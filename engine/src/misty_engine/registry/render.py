"""Pages are pure renderings of the registry (spec 3.4). This module is the only writer of entity pages and of
the People and Links sections of source notes.

A page exists only for an `active` entity that has a real summary and at least one public source. A "(stub)"
page cannot be produced here. `lint` counts the invariants; `repair` fixes the deterministic ones with no model."""
from __future__ import annotations

import json
import re
from collections import Counter

from ..clock import sgt_date
from ..ctx import Ctx
from ..wiki.notes import (FOLDER_FOR, entity_path, links_in, parse_entity, render_entity as _join_sections,
                          section, slugify)
from . import privacy
from .names import core_tokens, norm, reject_reason
from .store import Registry

TIMELINE_MAX = 80
NOW_MAX = 5
TOP_LINKS = 5
ENTITY_FOLDERS = ("people", "projects", "topics")
_STUB = {"", "(stub)"}
_PEOPLE_LINKS = re.compile(r"^## (People|Links)\n.*?(?=^## |\Z)", re.S | re.M)


# ------------------------------------------------------------------ data

def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _first_sentence(text: str, limit: int = 200) -> str:
    t = _clean(text)
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    return (m.group(1) if m else t)[:limit]


def _public_items(ctx: Ctx, eid: str, include_private: bool = False) -> list:
    """Items that mention the entity and are not in a private chat, newest first. `include_private` is only for
    a caller that asked for private notes by name (context(include_private=True))."""
    keys = privacy.private_keys(ctx.settings)
    rows = ctx.con.execute(
        "SELECT DISTINCT i.id, i.source, i.title, i.note_path, i.modified_at, i.meta FROM mentions m "
        "JOIN items i ON i.id = m.item_id WHERE m.entity_id=? ORDER BY i.modified_at DESC, i.id", (eid,)).fetchall()
    if include_private:
        return rows
    return [r for r in rows if not privacy.item_is_private(r["source"], json.loads(r["meta"] or "{}"), keys)]


def _text_row(ctx: Ctx, eid: str):
    return ctx.con.execute("SELECT * FROM entity_text WHERE entity_id=?", (eid,)).fetchone()


def _timeline(ctx: Ctx, reg: Registry, e, items: list, today: str) -> list[str]:
    names = {a["alias_norm"] for a in reg.aliases(e["id"])} | {norm(e["name"])}
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for r in items:
        meta = json.loads(r["meta"] or "{}")
        if not r["note_path"]:
            continue
        people = meta.get("people") or []
        crowded = e["kind"] == "person" and len(people) > 2
        dated = [(d, t) for d, t in (meta.get("dated_lines") or [])]
        if not dated:
            page = ctx.wiki.read(r["note_path"]) if ctx.wiki else None
            summary = _first_sentence(section(page.body, "Summary")) if page else ""
            note_date = (r["modified_at"] or today)[:10]
            dated = [(note_date, summary)] if summary else []
        for d, text in dated:
            text = _clean(text)
            if not text:
                continue
            if crowded and not any(n and n in norm(text) for n in names):
                continue
            k = (d, norm(text))
            if k in seen:
                continue
            seen.add(k)
            out.append((d, f"- {d}: {text} ([[{r['note_path'][:-3]}]])"))
    out.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [line for _, line in out[:TIMELINE_MAX]]


def _co_mentioned(ctx: Ctx, eid: str, item_ids: list[str]) -> list[str]:
    counts: Counter = Counter()
    for i in range(0, len(item_ids), 400):
        chunk = item_ids[i:i + 400]
        for r in ctx.con.execute(
                f"SELECT e.path AS path, COUNT(DISTINCT m.item_id) AS n FROM mentions m "
                f"JOIN entities e ON e.id = m.entity_id WHERE m.item_id IN ({','.join('?' * len(chunk))}) "
                f"AND m.entity_id != ? AND e.status='active' AND e.path IS NOT NULL GROUP BY e.path",
                (*chunk, eid)):
            counts[r["path"]] += r["n"]
    return [p for p, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_LINKS]]


# ------------------------------------------------------------------ rendering

def new_path(reg: Registry, kind: str, name: str, exists=None) -> str:
    """`people/<slug>.md`, with -2, -3 ... on a path another entity or a redirect already owns."""
    folder = FOLDER_FOR.get(kind, kind + "s")
    base = slugify(name)
    n = 1
    while True:
        p = f"{folder}/{base}{'' if n == 1 else f'-{n}'}.md"
        taken = (reg.con.execute("SELECT 1 FROM entities WHERE path=?", (p,)).fetchone()
                 or reg.con.execute("SELECT 1 FROM entity_redirects WHERE old_path=?", (p,)).fetchone()
                 or (exists is not None and exists(p)))
        if not taken:
            return p
        n += 1


def render_entity(ctx: Ctx, eid: str) -> tuple[dict, str] | None:
    """Frontmatter and body for an active entity with a real summary and a public source, else None."""
    reg = Registry(ctx.con, ctx.clock)
    e = reg.get(eid)
    if e is None or e["status"] != "active":
        return None
    t = _text_row(ctx, eid)
    summary = _clean(t["summary"]) if t else ""
    if summary in _STUB:
        return None
    items = _public_items(ctx, eid)
    if not items:
        return None
    today = sgt_date(ctx.clock.now())
    now = [b for b in json.loads(t["now"] or "[]") if isinstance(b, str) and b.strip()][:NOW_MAX]
    others = [a["alias"] for a in reg.aliases(eid) if norm(a["alias"]) != norm(e["name"])]
    from . import facts as F
    facts = {k: v for k, v in F.compute(ctx, eid).items() if v not in (None, [], "")}
    from ..wiki.areas import assign_area
    area = assign_area(ctx, eid)
    if area != e["area"]:
        ctx.con.execute("UPDATE entities SET area=? WHERE id=?", (area, eid))
    fm = {"type": e["kind"], "title": e["name"], "entity_id": eid, "aliases": others[:12], "area": area,
          "importance": e["importance"], "updated": today, "sources": [r["id"] for r in items],
          "sensitivity": "normal", "facts": facts, "summary_n": t["summary_n"]}
    parts = {
        "Summary": summary,
        "Now": "\n".join(f"- {_clean(b)}" for b in now),
        "Timeline": "\n".join(_timeline(ctx, reg, e, items, today)),
        "Links": "\n".join(f"- [[{p[:-3]}]]" for p in _co_mentioned(ctx, eid, [r["id"] for r in items])),
    }
    return fm, _join_sections(parts)


def write_entity(ctx: Ctx, eid: str) -> str | None:
    """Write the page (one wiki commit), keep the entity's path and the page embedding in step. None = no page."""
    reg = Registry(ctx.con, ctx.clock)
    out = render_entity(ctx, eid)
    if out is None:
        return None
    fm, body = out
    e = reg.get(eid)
    path = e["path"] or new_path(reg, e["kind"], e["name"], exists=ctx.wiki.exists)
    old = ctx.wiki.read(path)
    def stable(d: dict) -> dict:                  # the day stamps are not content: a new day alone rewrites nothing
        out = {k: v for k, v in d.items() if k != "updated"}
        out["facts"] = {k: v for k, v in (out.get("facts") or {}).items() if k != "as_of"}
        return out
    same = old is not None and old.body.strip() == body.strip() and stable(old.fm) == stable(fm)
    if not same:                           # an unchanged page is not rewritten: no commit, no log line, no new day stamp
        with ctx.wiki.batch(f"entity: {path[:-3]}"):
            ctx.wiki.write(path, fm, body)
        _embed(ctx, eid, path, fm, body)
    if e["path"] != path:
        reg.set_path(eid, path)
    ctx.extras = {**(ctx.extras or {}), "index_dirty": True}
    return path


def _embed(ctx: Ctx, eid: str, path: str, fm: dict, body: str) -> None:
    if ctx.embedder is None or ctx.index is None:
        return
    text = " ".join([fm["title"], *fm.get("aliases", []), section(body, "Summary")])
    vec = ctx.embedder.embed_documents([text])[0]
    ctx.index.upsert_page(path, text, vec)


def _entity_files(ctx: Ctx, eid: str) -> list[str]:
    """Where this entity's page may be on disk: its path and, if merged, the path it had before."""
    reg = Registry(ctx.con, ctx.clock)
    out: list[str] = []
    e = reg.get(eid)
    if e is not None and e["path"]:
        out.append(e["path"])
    for r in ctx.con.execute("SELECT snapshot FROM entity_merges WHERE dropped=?", (eid,)):
        p = (json.loads(r["snapshot"]).get("drop") or {}).get("path")
        if p:
            out.append(p)
    return list(dict.fromkeys(out))


def drop_page(ctx: Ctx, eid: str) -> None:
    """Remove the entity's file and its index row. The redirect (if any) stays so old links still resolve."""
    for path in _entity_files(ctx, eid):
        page = ctx.wiki.read(path)
        if page is not None and page.fm.get("entity_id") in (eid, None):
            with ctx.wiki.batch(f"entity removed: {path[:-3]}"):
                ctx.wiki.delete(path)
            if ctx.index is not None:
                ctx.index.remove_page(path)
            ctx.extras = {**(ctx.extras or {}), "index_dirty": True}


# ------------------------------------------------------------------ source notes

def relink_note(ctx: Ctx, note_path: str, item_id: str) -> bool:
    """People and Links of a source note point only at pages that exist; everything else is plain text.
    Names the resolver rejected (a phone number, a chat member) are left out. True if the note changed."""
    page = ctx.wiki.read(note_path)
    row = ctx.con.execute("SELECT meta, title, source FROM items WHERE id=?", (item_id,)).fetchone()
    if page is None or row is None:
        return False
    meta = json.loads(row["meta"] or "{}")
    reg = Registry(ctx.con, ctx.clock)
    by_raw = {r["raw"]: r["entity_id"] for r in ctx.con.execute(
        "SELECT raw, entity_id FROM mentions WHERE item_id=?", (item_id,))}
    title = (row["title"] or "").split(" · ")[0] if meta.get("chat_id") else ""
    kind_chat = meta.get("chat_kind") or ""

    def bullet(kind: str, raw: str) -> str | None:
        eid = by_raw.get(raw)
        if eid:
            s = reg.get(reg.survivor(eid))
            if s is not None and s["status"] == "active" and s["path"] and ctx.wiki.exists(s["path"]):
                return f"- [[{s['path'][:-3]}]]"
            return f"- {_clean(raw)}"
        if reject_reason(raw, kind, chat_title=title, chat_kind=kind_chat):
            return None
        return f"- {_clean(raw)}"

    def lines(kind: str, key: str) -> list[str]:
        out = [bullet(kind, r) for r in meta.get(key) or [] if isinstance(r, str) and slugify(r) != "untitled"]
        return [b for b in out if b]

    people = "\n".join(lines("person", "people")) or "- (none)"
    links = "\n".join(lines("project", "projects") + lines("topic", "topics")) or "- (none)"
    body = page.body
    new = _PEOPLE_LINKS.sub(lambda m: f"## {m.group(1)}\n{people if m.group(1) == 'People' else links}\n\n", body)
    if new.rstrip() == body.rstrip():
        return False
    ctx.wiki.write(note_path, page.fm, new.rstrip() + "\n")
    return True


def refresh_links(ctx: Ctx, eids: list[str]) -> int:
    """Rewrite `## Links` on the pages of those entities (top 5 co-mentioned active entities)."""
    changed = 0
    reg = Registry(ctx.con, ctx.clock)
    with ctx.wiki.batch("entity: refresh links"):
        for eid in eids:
            e = reg.get(eid)
            if e is None or e["status"] != "active" or not e["path"]:
                continue
            page = ctx.wiki.read(e["path"])
            if page is None:
                continue
            new = "\n".join(f"- [[{p[:-3]}]]" for p in _co_mentioned(
                ctx, eid, [r["id"] for r in _public_items(ctx, eid)]))
            parts = parse_entity(page.body)
            if parts["Links"].strip() != new:
                parts["Links"] = new
                ctx.wiki.write(e["path"], page.fm, _join_sections(parts))
                changed += 1
    return changed


# ------------------------------------------------------------------ invariants

def _entity_pages(ctx: Ctx):
    for folder in ENTITY_FOLDERS:
        for rel in ctx.wiki.all_pages():
            if rel.startswith(folder + "/"):
                p = ctx.wiki.read(rel)
                if p is not None and p.fm.get("type") in ("person", "project", "topic"):
                    yield rel, p


def lint(ctx: Ctx) -> dict[str, int]:
    """Counts by invariant. Never calls a model, never writes."""
    reg = Registry(ctx.con, ctx.clock)
    out = dict.fromkeys(("dead_links", "stub_pages", "unsummarised_2plus", "multi_token_alias_collisions",
                         "few_sources", "chat_name_people", "orphan_files", "missing_files"), 0)
    exists = {rel[:-3] for rel in ctx.wiki.all_pages()}
    active = {r["id"]: r for r in reg.entities(statuses=("active",))}
    for rel in ctx.wiki.all_pages():
        if rel.split("/")[0] in (*ENTITY_FOLDERS, "areas", "sources"):
            page = ctx.wiki.read(rel)
            if page is not None:
                out["dead_links"] += sum(1 for l in links_in(page.body) if l not in exists)
    on_disk: set[str] = set()
    stub_files: set[str] = set()
    for rel, p in _entity_pages(ctx):
        on_disk.add(rel)
        eid = p.fm.get("entity_id")
        if not eid or eid not in active:
            out["orphan_files"] += 1
        if _clean(section(p.body, "Summary")) in _STUB:
            stub_files.add(rel)
            out["stub_pages"] += 1
            if len(p.fm.get("sources") or []) >= 2:
                out["unsummarised_2plus"] += 1
    titles = {norm((r["title"] or "").split(" · ")[0]) for r in ctx.con.execute(
        "SELECT DISTINCT title FROM items WHERE kind='chat_week'")}
    for eid, e in active.items():
        n_public = len(_public_items(ctx, eid))
        if e["path"] and e["path"] not in on_disk and not ctx.wiki.exists(e["path"]):
            out["missing_files"] += 1
        t = _text_row(ctx, eid)
        if (t is None or _clean(t["summary"]) in _STUB) and e["path"] and e["path"] not in stub_files:
            out["stub_pages"] += 1
            if n_public >= 2:
                out["unsummarised_2plus"] += 1
        if n_public < 2 and not _is_known(e):
            out["few_sources"] += 1
        if e["kind"] == "person" and (reject_reason(e["name"], "person")
                                      or (norm(e["name"]) in titles and not _is_known(e))):   # "Mamma" is a 1:1 chat's title too
            out["chat_name_people"] += 1
    out["multi_token_alias_collisions"] = ctx.con.execute(
        "SELECT COUNT(*) FROM (SELECT a.kind, a.alias_norm FROM entity_aliases a JOIN entities e ON e.id = a.entity_id "
        "WHERE e.status='active' AND a.alias_norm LIKE '% %' GROUP BY a.kind, a.alias_norm "
        "HAVING COUNT(DISTINCT a.entity_id) > 1)").fetchone()[0]
    return out


def _is_known(e) -> bool:
    return bool(e["known"]) or e["origin"] in ("contact", "user")


def legacy_pages(ctx: Ctx) -> int:
    """Entity pages written before the registry existed (no `entity_id`). While any exist the wiki has not been
    migrated: note links point at those pages, so nothing may be relinked or deleted yet (`registry rebuild`)."""
    return sum(1 for _, p in _entity_pages(ctx) if not p.fm.get("entity_id"))


def _repoint_area_links(ctx: Ctx) -> int:
    """Area pages list entity pages under `## Links`. A link to a page that is gone follows its redirect to the
    survivor, or is dropped when there is none. Returns the number of area pages changed."""
    reg = Registry(ctx.con, ctx.clock)
    changed = 0
    with ctx.wiki.batch("migration: repoint area links"):
        for rel in ctx.wiki.all_pages():
            page = ctx.wiki.read(rel) if rel.startswith("areas/") else None
            if page is None:
                continue
            parts = parse_entity(page.body)
            keep: list[str] = []
            for target in links_in(parts["Links"]):
                if not ctx.wiki.exists(target + ".md"):
                    eid = reg.redirect(target + ".md")
                    s = reg.get(eid) if eid else None
                    target = s["path"][:-3] if s is not None and s["status"] == "active" and s["path"] \
                        and ctx.wiki.exists(s["path"]) else ""
                if target and target not in keep:
                    keep.append(target)
            new = "\n".join(f"- [[{t}]]" for t in keep)
            if new != parts["Links"].strip():
                parts["Links"] = new
                ctx.wiki.write(rel, page.fm, _join_sections(parts))
                changed += 1
    return changed


def adopt_legacy_pages(ctx: Ctx) -> dict[str, int]:
    """Finish a migration: `registry rebuild` moves the entities into the registry but leaves the old pages on disk.
    A legacy page that is an active entity's own page is re-rendered (it gets its `entity_id`); every other one is
    removed (a merged name keeps resolving through `entity_redirects`, a rejected name has no entity). Then `repair`
    points the notes at the survivors. No model. Safe to run twice."""
    reg = Registry(ctx.con, ctx.clock)
    active_by_path = {r["path"]: r["id"] for r in reg.entities(statuses=("active",)) if r["path"]}
    legacy = [rel for rel, p in _entity_pages(ctx) if not p.fm.get("entity_id")]
    out = {"stamped": 0, "removed": 0, "demoted": 0}
    gone: list[str] = []
    for rel in legacy:
        eid = active_by_path.get(rel)
        if eid and write_entity(ctx, eid):
            out["stamped"] += 1
            continue
        if eid:                      # active, but no public note names it any more: it cannot have a page
            ctx.con.execute("INSERT OR REPLACE INTO entity_redirects(old_path, entity_id) VALUES (?,?)", (rel, eid))
            reg.set_path(eid, None)
            reg.set_status(eid, "candidate")
            out["demoted"] += 1
        gone.append(rel)
    if gone:
        with ctx.wiki.batch(f"migration: remove {len(gone)} pre-registry pages"):
            for rel in gone:
                ctx.wiki.delete(rel)
        for rel in gone:
            if ctx.index is not None:
                ctx.index.remove_page(rel)
        ctx.extras = {**(ctx.extras or {}), "index_dirty": True}
        out["removed"] = len(gone)
    fixed = repair(ctx)                              # no legacy page is left, so this relinks the notes
    out["legacy_pages"] = fixed["legacy_pages"]
    out["notes_relinked"] = fixed["notes_relinked"]
    out["area_pages_repointed"] = _repoint_area_links(ctx)
    return out


def repair(ctx: Ctx) -> dict[str, int]:
    """Deterministic repairs: re-render missing pages, drop pages whose entity is gone, relink notes. No model.
    A file with no `entity_id` is a pre-registry page: it is reported by lint and left alone."""
    reg = Registry(ctx.con, ctx.clock)
    fixed = {"pages_dropped": 0, "pages_rendered": 0, "notes_relinked": 0, "legacy_pages": legacy_pages(ctx)}
    if fixed["legacy_pages"]:
        return fixed                           # an unmigrated wiki: leave every page and link as it is
    active = {r["id"] for r in reg.entities(statuses=("active",))}
    for rel, p in list(_entity_pages(ctx)):
        eid = p.fm.get("entity_id")
        if eid and eid not in active:
            with ctx.wiki.batch(f"entity removed: {rel[:-3]}"):
                ctx.wiki.delete(rel)
            if ctx.index is not None:
                ctx.index.remove_page(rel)
            fixed["pages_dropped"] += 1
    for e in reg.entities(statuses=("active",)):
        if e["path"] and not ctx.wiki.exists(e["path"]) and write_entity(ctx, e["id"]):
            fixed["pages_rendered"] += 1
    with ctx.wiki.batch("repair: relink notes"):
        for r in ctx.con.execute("SELECT id, note_path FROM items WHERE note_path IS NOT NULL").fetchall():
            if relink_note(ctx, r["note_path"], r["id"]):
                fixed["notes_relinked"] += 1
    return fixed
