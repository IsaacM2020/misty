#!/usr/bin/env python3
"""Print what is needed to draft the golden set from the wiki. Read-only. Titles and paths only, no body text.

Usage:
  draft_golden.py [WIKI_ROOT]                 print the three lists (default root ~/Misty/wiki)
  draft_golden.py [WIKI_ROOT] --write OUTDIR  also write OUTDIR/golden.yaml and OUTDIR/golden.dups.yaml drafts.
                                              References are copied by the script from each page's own summary;
                                              nobody has to read them before Isaac edits and approves the files.
"""
from __future__ import annotations

import datetime
import re
import sys
import unicodedata
from pathlib import Path

import yaml

FOLDERS = ("people", "projects", "topics")
DATED = re.compile(r"^\s*-\s*(\d{4}-\d{2}-\d{2}|\d{4}-W\d{2})\b")


def norm(s: str) -> tuple[str, ...]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return tuple(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def read(p: Path):
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


def main(root: Path) -> None:
    pages = []
    for folder in FOLDERS:
        for p in sorted((root / folder).glob("*.md")) if (root / folder).is_dir() else ():
            r = read(p)
            if r:
                fm, body = r
                pages.append((folder, p.stem, str(fm.get("title", p.stem)), len(fm.get("sources") or []), body))
    print("## a) 40 entity pages with the most sources: path | title | sources")
    for folder, stem, title, n, _ in sorted(pages, key=lambda x: -x[3])[:40]:
        print(f"{folder}/{stem} | {title} | {n}")
    print("\n## b) strict-subset name groups, same folder (possible duplicates): paths")
    for folder in FOLDERS:
        rows = [(f"{f}/{s}", norm(t)) for f, s, t, _, _ in pages if f == folder]
        for i, (pa, a) in enumerate(rows):
            subs = [pb for pb, b in rows if b and a and a != b and set(a) < set(b)]
            if subs and len(a) >= 1:
                print(f"{pa}  <  {', '.join(subs[:6])}")
    print("\n## c) 20 most recent dated Timeline lines: date | page path (text not printed)")
    dated = []
    for folder, stem, _, _, body in pages:
        for line in body.splitlines():
            m = DATED.match(line)
            if m:
                dated.append((m.group(1), f"{folder}/{stem}"))
    today = datetime.date.today().isoformat()
    for d, path in sorted((x for x in dated if x[0][:4].isdigit() and x[0] <= today), reverse=True)[:20]:
        print(f"{d} | {path}")


# canonical page per duplicate family, so the golden expects one path
HARD = [("Pranav", "people/pranav-asrani"), ("Ms Janaki", "people/ms-janaki"),
        ("SAT practice", "topics/sat-practice"), ("iGEM wiki deadline", "projects/igem-wiki"),
        ("exam schedule", "topics/exam-schedule")]
GROUPS = [
    {"name": "pranav", "members": ["Pranav", "Pranav Asrani", "Pranav Naveen Asrani", "Pranav Asrani Real"]},
    {"name": "ms-janaki", "members": ["Janaki", "Ms Janaki", "Janaki Kota", "Ms Janaki Kota"]},
    {"name": "sat", "kind": "topic", "members": ["SAT practice", "SAT preparation", "SAT prep"]},
    {"name": "exam-dates", "kind": "topic", "members": ["exam dates", "exam schedule", "exam timetable"]},
]
APART = [["Janaki Kota", "Janaki Subramanian"], ["Shriya Ravi", "Shriya Vishwanath"],
         ["Ananya Garg", "Ananya Pai"], ["Naveen Asrani", "Pranav Asrani"]]


def first_sentence(body: str) -> str:
    m = re.search(r"^## Summary\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    s = " ".join((m.group(1) if m else "").split())
    if s in ("", "(stub)"):
        return ""
    cut = re.split(r"(?<=[.!?])\s", s, maxsplit=1)[0]
    return cut[:240]


def write_drafts(root: Path, out: Path) -> None:
    rows = []
    for folder in FOLDERS:
        for p in sorted((root / folder).glob("*.md")) if (root / folder).is_dir() else ():
            r = read(p)
            if r:
                fm, body = r
                rows.append({"path": f"{folder}/{p.stem}", "title": str(fm.get("title", p.stem)),
                             "n": len(fm.get("sources") or []), "type": folder, "ref": first_sentence(body)})
    hard_paths = {p for _, p in HARD}
    family = {"people/pranav", "people/pranav-asrani-real", "people/pranav-naveen-asrani", "people/janaki",
              "people/janaki-kota", "people/ms-janaki-kota", "people/janaki-s", "people/janaki-subramanian",
              "topics/sat-prep", "topics/sat-preparation", "topics/exam-timetable", "topics/exam-dates"}
    pool = [r for r in sorted(rows, key=lambda r: -r["n"])
            if r["ref"] and r["n"] >= 5 and not r["path"].startswith("people/isaac")
            and r["path"] not in hard_paths and r["path"] not in family]
    cases: list[dict] = []
    for i, (q, path) in enumerate(HARD, 1):
        cases.append({"id": f"g{i:02d}", "kind": "search", "q": q, "expect_entities": [path]})
    n = len(cases)
    for r in pool[:10]:
        n += 1
        cases.append({"id": f"g{n:02d}", "kind": "search", "q": r["title"], "expect_entities": [r["path"]]})
    for r in pool[10:25]:
        n += 1
        q = f"who is {r['title']}" if r["type"] == "people" else f"what is going on with {r['title']}"
        cases.append({"id": f"g{n:02d}", "kind": "ask", "q": q, "expect_entities": [r["path"]],
                      "reference": r["ref"]})
    for r in pool[25:35]:
        n += 1
        cases.append({"id": f"g{n:02d}", "kind": "pack", "q": f"I need context on {r['title']}",
                      "expect_entities": [r["path"]]})
    out.mkdir(parents=True, exist_ok=True)
    head = "# DRAFT, not yet approved by Isaac. Edit freely. Gitignored (personal data).\n"
    (out / "golden.yaml").write_text(head + yaml.safe_dump(cases, sort_keys=False, allow_unicode=True, width=100))
    (out / "golden.dups.yaml").write_text(
        head + "# groups: each list must end as ONE entity. apart: pairs that must NEVER merge.\n"
        + yaml.safe_dump({"groups": GROUPS, "apart": APART}, sort_keys=False, allow_unicode=True, width=100))
    print(f"wrote {len(cases)} cases and {len(GROUPS)} groups, {len(APART)} apart pairs to {out}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    wiki = Path(args[0]) if args and "--write" not in sys.argv else Path.home() / "Misty" / "wiki"
    if "--write" in sys.argv:
        write_drafts(wiki, Path(sys.argv[sys.argv.index("--write") + 1]))
    else:
        main(wiki)
