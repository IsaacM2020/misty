"""Wiki note format: YAML frontmatter + markdown body. The LLM never writes YAML or link syntax; code does."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import yaml

from ..compile.haiku import NoteDraft
from ..errors import MistyError

ENTITY_SECTIONS = ("Summary", "Now", "Timeline", "Links")
SOURCE_SECTIONS = ("Summary", "Key facts", "People", "Links")
ENTITY_TYPES = ("person", "project", "topic", "area")
FOLDER_FOR = {"person": "people", "project": "projects", "topic": "topics", "area": "areas"}
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
# The same safe parser written in C (libyaml): about 10x faster, which matters because a chat message reads ~100 pages.
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
TIMELINE_RE = re.compile(r"^- \d{4}-\d{2}-\d{2}: .+$")


class NoteError(MistyError):
    pass


@dataclass
class Page:
    path: str
    fm: dict
    body: str


_TRANSLIT = str.maketrans({"\u00f8": "o", "\u00d8": "O", "\u00e6": "ae", "\u00c6": "AE", "\u00df": "ss",
                           "\u0111": "d", "\u0110": "D", "\u0142": "l", "\u0141": "L", "\u0153": "oe",
                           "\u00fe": "th"})


def slugify(text: str, *, max_len: int = 60) -> str:
    text = text.translate(_TRANSLIT)
    # non-ASCII punctuation, symbols and emoji separate words (curly apostrophe, dashes, 🏏) instead of vanishing
    text = "".join(" " if (ord(c) > 127 and unicodedata.category(c)[0] in "PSZ") else c for c in text)
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    s = s[:max_len].strip("-")
    return s or "untitled"


def source_note_path(source: str, item_id: str, title: str) -> str:
    return f"sources/{slugify(source, max_len=20)}/{slugify(title)}-{item_id[-6:]}.md"


def _clean(v):
    if isinstance(v, str):
        return " ".join(v.split())
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def dump_page(fm: dict, body: str) -> str:
    head = yaml.safe_dump(_clean(fm), sort_keys=False, allow_unicode=True, default_flow_style=None, width=1000)
    return f"---\n{head}---\n\n{body.strip()}\n"


def parse_page(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise NoteError("FRONTMATTER")
    end = text.find("\n---\n", 4)
    if end == -1:
        if text.endswith("\n---"):
            end = len(text) - 4
        else:
            raise NoteError("FRONTMATTER")
    try:
        fm = yaml.load(text[4:end + 1], Loader=_SAFE_LOADER)
    except yaml.YAMLError:
        raise NoteError("FRONTMATTER")
    if not isinstance(fm, dict):
        raise NoteError("FRONTMATTER")
    return fm, text[end + 5:].strip("\n")


def links_in(body: str) -> list[str]:
    return [m.group(1).strip() for m in LINK_RE.finditer(body)]


def section(body: str, name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    return m.group(1).strip("\n") if m else ""


def sections_in(body: str) -> list[str]:
    return re.findall(r"^## (.+)$", body, re.M)


def entity_path(kind: str, name: str) -> str:
    return f"{FOLDER_FOR[kind]}/{slugify(name)}.md"


def render_source_note(draft: NoteDraft, *, item_id: str, source: str, today: str) -> tuple[dict, str]:
    fm = {"type": "source", "title": " ".join(draft.title.split()), "item_id": item_id, "source": source,
          "updated": today}
    lines = ["## Summary", draft.summary.strip(), "", "## Key facts"]
    lines += [f"- {f}" for f in draft.key_facts] or ["- (none)"]
    lines += ["", "## People"]
    lines += [f"- [[people/{slugify(p)}]]" for p in draft.people] or ["- (none)"]
    lines += ["", "## Links"]
    links = [f"- [[projects/{slugify(p)}]]" for p in draft.projects] + \
            [f"- [[topics/{slugify(t)}]]" for t in draft.topics]
    lines += links or ["- (none)"]
    return fm, "\n".join(lines)


def validate_page(fm: dict, body: str) -> list[str]:
    errs: list[str] = []
    typ = fm.get("type")
    if not all(k in fm for k in ("type", "title", "updated")):
        errs.append("FM_KEYS")
    elif not isinstance(fm["title"], str) or not isinstance(typ, str):
        errs.append("FM_TYPE")
    if typ in ("source",):
        want = SOURCE_SECTIONS
    elif typ in ENTITY_TYPES:
        want = ENTITY_SECTIONS
    else:
        return errs
    have = sections_in(body)
    if any(s not in have for s in want):
        errs.append("SECTIONS_MISSING")
    elif [s for s in have if s in want] != list(want):
        errs.append("SECTIONS_ORDER")
    if typ in ENTITY_TYPES and "SECTIONS_MISSING" not in errs:
        now = [l for l in section(body, "Now").splitlines() if l.startswith("- ")]
        if len(now) > 5:
            errs.append("NOW_TOO_LONG")
        tl = [l for l in section(body, "Timeline").splitlines() if l.strip()]
        if any(not TIMELINE_RE.match(l) for l in tl):
            errs.append("TIMELINE_FORMAT")
        srcs = fm.get("sources", [])
        if srcs is not None and not isinstance(srcs, list):
            errs.append("SOURCES_FORMAT")
    return errs


def entity_skeleton(kind: str, title: str, today: str, item_id: str | None = None) -> tuple[dict, str]:
    fm = {"type": kind, "title": " ".join(title.split()), "aliases": [], "area": None, "importance": 3,
          "updated": today, "sources": [item_id] if item_id else []}
    body = "## Summary\n(stub)\n\n## Now\n\n## Timeline\n\n## Links\n"
    return fm, body


def item_entities(meta: dict) -> list[tuple[str, str]]:
    """(kind, name) pairs a compiled note names, in meta order, deduplicated by page path."""
    out, seen = [], set()
    for key, kind in (("people", "person"), ("projects", "project"), ("topics", "topic")):
        for name in meta.get(key, []) or []:
            path = entity_path(kind, name)
            if path not in seen and slugify(name) != "untitled":
                seen.add(path)
                out.append((kind, name))
    return out


def parse_entity(body: str) -> dict[str, str]:
    return {name: section(body, name) for name in ENTITY_SECTIONS}


def render_entity(parts: dict[str, str]) -> str:
    out = []
    for name in ENTITY_SECTIONS:
        text = (parts.get(name) or "").strip("\n")
        out.append(f"## {name}\n{text}\n" if text else f"## {name}\n")
    return "\n".join(out)
