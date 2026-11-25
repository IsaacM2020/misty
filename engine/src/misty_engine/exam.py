"""Season 2 exam runner: ask the wiki each exam question and save what Misty answered and what she was shown.

Read-only on the wiki: answers are NOT filed back (a second run must not read the first run's answers).
Each call is one Haiku call Isaac asked for himself, so it runs inside user_approved() (passes the paid lock).
Scoring is not done here. The output has blank 'key' and 'verdict' fields for the answer-key step.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .clock import sgt_date
from .ctx import Ctx
from .errors import MistyError
from .openrouter import user_approved
from .search import search
from .wiki.ask import ask_wiki
from .wiki.context import build_context

QUESTION_RE = re.compile(r"^(\d{1,2})\. \[([A-Z])\] (.+)$")


def load_questions(path: Path) -> list[dict]:
    """Parse '1. [S] text' lines from the exam markdown."""
    out = []
    for line in path.read_text().splitlines():
        m = QUESTION_RE.match(line.strip())
        if m:
            out.append({"n": int(m[1]), "tag": m[2], "q": m[3].strip()})
    return out


async def _via_chat(ctx: Ctx, text: str) -> dict:
    """The real chat (tools: recall, class notes, calendar...), read-only: nothing saved, nothing opened or changed."""
    from . import chat

    async def quiet(_: str) -> None:
        return None

    async def no(_: str) -> bool:
        return False
    return await chat.send(ctx, text, on_delta=quiet, on_status=quiet, confirm=no, exam=True)


async def run_exam(ctx: Ctx, questions: list[dict], only: list[int] | None = None, via: str = "wiki") -> dict:
    rows = []
    total = 0.0
    for q in questions:
        if only and q["n"] not in only:
            continue
        row = {**q, "answer": None, "cites": [], "usd": 0.0, "error": None,
               "pack_pages": [], "search_hits": [], "key": "", "verdict": "", "cause": ""}
        try:
            # What the model was shown (local, free). Needed later to tell "search didn't find it" from "model got it wrong".
            pack = build_context(ctx, q["q"], 3000)
            row["pack_pages"] = list(pack.pages)
            row["search_hits"] = [f"{h.source}: {h.title}" for h in search(ctx, q["q"], limit=8, exclude_private=True)]
            if via == "chat":
                r = await _via_chat(ctx, q["q"])
                row.update(answer=r["reply"], usd=r["usd"],
                           cites=[f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)[:80]})" for c in r["calls"]])
                total += r["usd"] or 0.0
            else:
                with user_approved():
                    a = await ask_wiki(ctx, q["q"], job_id=None, file_answer=False)
                row.update(answer=a.answer, cites=a.citations, usd=a.usd)
                total += a.usd or 0.0
        except MistyError as e:
            row["error"] = e.code
        rows.append(row)
    return {"ran_at": datetime.now().isoformat(timespec="seconds"), "sgt_date": sgt_date(ctx.clock.now()), "via": via,
            "total_usd": round(total, 5), "rows": rows}


def write_reports(result: dict, out_dir: Path, label: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{result['sgt_date']}-{label}"
    jp, mp = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    jp.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    lines = [f"# Season 2 exam run: {label}", "",
             f"Ran {result['ran_at']}. Cost ${result['total_usd']:.4f}. Not scored yet (answer key comes next).", ""]
    for r in result["rows"]:
        lines += [f"## Q{r['n']} [{r['tag']}] {r['q']}", ""]
        if r["error"]:
            lines += [f"**ERROR:** {r['error']}", ""]
            continue
        lines += [r["answer"] or "(empty)", "",
                  ("Tools used: " if result.get("via") == "chat" else "Cites: ") + ((", ".join(r["cites"]) if result.get("via") == "chat" else ", ".join(f"[[{c}]]" for c in r["cites"])) or "none"),
                  "Pages shown to the model: " + (", ".join(r["pack_pages"][:8]) or "none"), ""]
    mp.write_text("\n".join(lines))
    return jp, mp
