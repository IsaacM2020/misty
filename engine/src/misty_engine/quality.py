"""Counts-only quality report over the wiki. Never returns names, titles or text."""
from __future__ import annotations
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
import yaml

FOLDERS = {"people": "person", "projects": "project", "topics": "topic"}
LINK = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
REJECT_PATTERNS = (re.compile(r"\bchat( member| members)?$", re.I), re.compile(r"\bgroup\b$", re.I),
                   re.compile(r"\bmembers?\b", re.I), re.compile(r"^\+?\d[\d\s().-]{5,}$"))


def _norm(s: str) -> tuple[str, ...]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return tuple(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def _read(p: Path):
    t = p.read_text()
    if not t.startswith("---\n"):
        return None
    end = t.find("\n---\n", 4)
    if end == -1:
        return None
    try:
        return yaml.safe_load(t[4:end + 1]) or {}, t[end + 5:]
    except yaml.YAMLError:
        return None


def report(wiki_root: Path, con) -> dict:
    pages, titles = {}, {}
    for folder in FOLDERS:
        for p in (wiki_root / folder).glob("*.md") if (wiki_root / folder).is_dir() else ():
            parsed = _read(p)
            if parsed and parsed[0].get("type") in FOLDERS.values():
                rel = f"{folder}/{p.stem}"
                pages[rel] = parsed
                titles.setdefault(folder, []).append((rel, _norm(str(parsed[0].get("title", "")))))
    exists = {p.relative_to(wiki_root).with_suffix("").as_posix() for p in wiki_root.rglob("*.md")}
    dead = stubs = stubs2 = chat_people = 0
    for rel, (fm, body) in pages.items():
        summary = re.search(r"^## Summary\n(.*?)(?=^## |\Z)", body, re.S | re.M)
        s = (summary.group(1).strip() if summary else "")
        n = len(fm.get("sources") or [])
        if s in ("", "(stub)"):
            stubs += 1
            stubs2 += n >= 2
        if rel.startswith("people/") and any(r.search(str(fm.get("title", ""))) for r in REJECT_PATTERNS):
            chat_people += 1
        dead += sum(1 for l in LINK.findall(body) if l.strip() not in exists)
    dup = 0
    for folder, rows in titles.items():
        for i, (_, a) in enumerate(rows):
            for _, b in rows[i + 1:]:
                if a and b and a != b and (set(a) < set(b) or set(b) < set(a)):
                    dup += 1
    names = Counter(t for rows in titles.values() for _, t in rows)
    notes = con.execute("SELECT COUNT(*) FROM items WHERE note_path IS NOT NULL").fetchone()[0]
    leaks = _private_leaks(con, pages)
    return {"entity_pages": len(pages), "by_type": dict(Counter(f["type"] for f, _ in pages.values())),
            "dup_pairs_subset": dup, "alias_collisions": sum(1 for c in names.values() if c > 1),
            "stub_pages": stubs, "stub_pages_2plus_sources": stubs2, "dead_links": dead,
            "chat_name_people": chat_people, "pack_dup_pages": None, "notes": notes, "private_leaks": leaks}


def _private_leaks(con, pages: dict) -> int:
    """Entity pages that cite an item of a chat kept out of context packs (as a source or a timeline link)."""
    row = con.execute("SELECT value FROM settings WHERE key='context_private_chats'").fetchone()
    keys = set(json.loads(row[0])) if row and row[0] else set()
    if not keys:
        return 0
    ids, paths = set(), set()
    for r in con.execute("SELECT id, source, note_path, meta FROM items WHERE json_extract(meta,'$.chat_id') IS NOT NULL"):
        if f"{r['source']}:{json.loads(r['meta'] or '{}').get('chat_id')}" in keys:
            ids.add(r["id"])
            if r["note_path"]:
                paths.add(r["note_path"][:-3])
    n = 0
    for fm, body in pages.values():
        if ids & set(fm.get("sources") or []) or any(l.strip() in paths for l in LINK.findall(body)):
            n += 1
    return n


def pack_duplicate_count(ctx, queries: list[str], budget: int = 2000) -> int:
    """How many packs for these queries hold two entries for one entity (same id, or the same normalised name).
    Zero is the contract; this is the measurement behind acceptance A3. Needs a live ctx (it builds packs)."""
    from .wiki.context import build_context
    bad = 0
    for q in queries:
        pack = build_context(ctx, q, budget)
        ids = [e.id for e in pack.entities]
        names = [(e.type, " ".join(_norm(e.name))) for e in pack.entities]
        if len(set(ids)) != len(ids) or len(set(names)) != len(names):
            bad += 1
    return bad
