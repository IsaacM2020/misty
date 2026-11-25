#!/usr/bin/env python3
"""E4 scored check: every `lost` item in his real class notes, and past help messages, matched to a topic. PAID (a few cents).

    engine/.venv/bin/python scripts/checks/fill_check.py --map "Chemistry=chem-syllabus.pdf" --map "Math=math.pdf" \
        [--helps help-lines.txt] [--max-usd 0.10]
    engine/.venv/bin/python scripts/checks/fill_check.py --dry      # free: a fixture, no model call, proves the script runs

The name before "=" is the subject name his classes use (class_sessions.subject); the file is that subject's syllabus.
Runs on a throwaway COPY of the live db (own spend meter), builds each map, then prints every lost item with the topic she
matched, numbered, for Isaac to mark: right topic or rightly none. Bars: lost items at least 85%, 20 help messages at least 17.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402


async def main_async(a) -> int:
    from misty_engine.openrouter import user_approved
    from misty_engine.teach import build as tb
    from misty_engine.teach import map as tm
    from misty_engine.teach import match
    from misty_engine.teach import sources as ts
    ctx, home = _harness.temp_ctx(copy_live=not a.dry)
    usd = 0.0
    try:
        if a.dry:                                              # fixture: one subject, no model call
            s = tm.add_subject(ctx, "demo", "Demo")["id"]
            tm.add_topic(ctx, s, "t", "A topic", {"source_id": "x", "chunk_no": 1, "quote": "a topic"})
            print("dry run ok: subject, topic and matcher import and run; no paid call made")
            return 0
        subj_of = {}
        for pair in a.map:
            name, _, path = pair.partition("=")
            src = await ts.add(ctx, path, "syllabus")
            sid = tm.add_subject(ctx, name, name)["id"]
            tm.link_class(ctx, sid, name)
            with user_approved():
                r = await tb.build_map(ctx, src["id"], sid)
            usd += r["usd"]
            subj_of[name.lower()] = sid
            print(f"map {name}: {r['status']}, {r['topics']} topics, ${r['usd']:.4f}")
        rows = ctx.con.execute("SELECT id, subject, notes_json FROM class_sessions WHERE notes_json IS NOT NULL AND merged_into IS NULL").fetchall()
        n = 0
        print("\nLOST ITEMS (mark each: right topic, or rightly none)")
        for r in rows:
            sid = subj_of.get(r["subject"].lower())
            if sid is None:
                continue
            for x in json.loads(r["notes_json"]).get("lost") or []:
                with user_approved():
                    tid, c = await match.topic_and_cost(ctx, sid, x["text"])
                usd += c
                n += 1
                title = ctx.con.execute("SELECT title FROM teach_topics WHERE id=?", (tid,)).fetchone()[0] if tid else "(none)"
                print(f"{n:>2}. [{r['subject']}] {x['text'][:110]}\n      -> {title}")
        if a.helps:
            print("\nHELP MESSAGES (mark each)")
            for i, line in enumerate(Path(a.helps).read_text().splitlines(), 1):
                if not line.strip():
                    continue
                with user_approved():
                    tid, c = await match.topic_and_cost(ctx, None, line)
                usd += c
                title = ctx.con.execute("SELECT title FROM teach_topics WHERE id=?", (tid,)).fetchone()[0] if tid else "(none)"
                print(f"{i:>2}. {line[:110]}\n      -> {title}")
        print(f"\ncost ${usd:.4f} (limit ${a.max_usd:.2f}); {n} lost items matched. Isaac marks them: bars 85% and 17 of 20.")
        return 0 if usd <= a.max_usd else 1
    finally:
        _harness.cleanup(home)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", action="append", default=[], help="CLASSSUBJECT=syllabus file")
    ap.add_argument("--helps", help="a text file with one past help message per line")
    ap.add_argument("--max-usd", type=float, default=0.10)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if not a.dry and not a.map:
        ap.error("give at least one --map, or --dry")
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    sys.exit(main())
