"""index.md: one line per entity page (`- [[path]] - first sentence of Summary`). Source notes are not listed."""
from __future__ import annotations

import re

from .notes import section

FOLDERS = ("areas", "projects", "people", "topics", "answers")


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    if not text or text == "(stub)":
        return "(stub)"
    m = re.match(r".+?[.!?](?:\s|$)", text)
    s = (m.group(0) if m else text).strip()
    return s if len(s) <= 140 else s[:137].rstrip() + "..."


def render_index(store) -> str:
    lines = ["# Index", ""]
    for folder in FOLDERS:
        entries = []
        for rel in store.all_pages():
            if not rel.startswith(folder + "/"):
                continue
            p = store.read(rel)
            if p is None:
                continue
            imp = p.fm.get("importance", 3)
            entries.append((-(imp if isinstance(imp, int) else 3), str(p.fm.get("title", rel)).lower(), rel, p))
        if not entries:
            continue
        lines += [f"## {folder.capitalize()}", ""]
        for _, _, rel, p in sorted(entries):
            summary = p.body if folder == "answers" else section(p.body, "Summary")
            lines.append(f"- [[{rel[:-3]}]] - {_first_sentence(summary)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_index(store) -> None:
    store.write_text("index.md", render_index(store))


def write_index_if_dirty(ctx) -> bool:
    """Rewrite index.md once after a page changed (`extras["index_dirty"]`), not again until the next change."""
    if not (ctx.extras or {}).get("index_dirty"):
        return False
    ctx.extras = {**(ctx.extras or {}), "index_dirty": False}
    new = render_index(ctx.wiki)
    p = ctx.paths.wiki / "index.md"
    if p.is_file() and p.read_text() == new:
        return False
    ctx.wiki.write_text("index.md", new)
    return True
