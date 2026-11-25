"""Where the money went: totals by job, by day, and message by message (every model call a reply made, its tokens,
the tools it asked for, what the tools themselves spent). All from Misty's own tables, nothing leaves the Mac."""
from __future__ import annotations

import json
from datetime import datetime

from .clock import SGT, sgt_day_start

LABELS = {
    "chat": "Chat replies", "chat_compact": "Chat memory summary",
    "stream": "Doing things on your Mac", "act": "Doing things on your Mac", "act_spike": "Doing things on your Mac",
    "act_escalate": "Doing things on your Mac",
    "class": "Class notes and answers", "judge": "Matching names and places", "summarise": "Summaries",
    "compile": "Writing notes from mail and chats", "eval": "Testing and tuning", "jev": "Reading mail and chats",
    "hear": "Reading mail and chats", "prep": "Preparing items", "about": "Your about card",
    "priorities": "Priorities", "areas": "Areas", "core": "Core notes",
    None: "Early build (before jobs were labelled)",
}


def label(purpose: str | None) -> str:
    return LABELS.get(purpose or None, purpose or LABELS[None])


def _window(now: float, days: int) -> float:
    return sgt_day_start(now) - (days - 1) * 86400


def breakdown(con, now: float, days: int = 7, messages: int = 20) -> dict:
    days = max(1, min(int(days), 90))
    since = _window(now, days)
    today = sgt_day_start(now)
    tot = lambda lo: float(con.execute("SELECT COALESCE(SUM(usd),0) FROM spend WHERE ts>=?", (lo,)).fetchone()[0])
    by_purpose: dict[str, dict] = {}
    for r in con.execute("SELECT purpose, model, COUNT(*) n, SUM(usd) usd FROM spend WHERE ts>=? GROUP BY purpose, model",
                         (since,)):
        g = by_purpose.setdefault(label(r["purpose"]), {"label": label(r["purpose"]), "calls": 0, "usd": 0.0, "models": []})
        g["calls"] += r["n"]
        g["usd"] += r["usd"]
        g["models"].append(f"{r['model'].split('/')[-1]} x{r['n']}")
    by_day = [{"day": datetime.fromtimestamp(r["d"], SGT).strftime("%a %d %b"), "usd": round(r["usd"], 4)}
              for r in con.execute("SELECT CAST((ts + 28800) / 86400 AS INT) * 86400 - 28800 d, SUM(usd) usd FROM spend "
                                   "WHERE ts>=? GROUP BY 1 ORDER BY 1 DESC", (since,))]
    return {"days": days, "today": round(tot(today), 4), "window": round(tot(since), 4),
            "by_purpose": sorted(({**g, "usd": round(g["usd"], 4)} for g in by_purpose.values()), key=lambda g: -g["usd"]),
            "by_day": by_day, "messages": message_costs(con, messages)}


def message_costs(con, limit: int) -> list[dict]:
    """The newest answers, each with the question that asked for it and the model calls inside it."""
    rows = con.execute("SELECT id, text, meta, created_at FROM chat_messages WHERE role='assistant' "
                       "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        q = con.execute("SELECT text FROM chat_messages WHERE role='user' AND id<? ORDER BY id DESC LIMIT 1",
                        (r["id"],)).fetchone()
        calls = meta.get("calls") or []
        out.append({
            "id": r["id"], "at": datetime.fromtimestamp(r["created_at"], SGT).strftime("%a %d %b %H:%M"),
            "question": (q["text"] if q else "")[:90], "reply": r["text"][:90],
            "usd": float(meta.get("usd") or 0),
            "rounds": meta.get("rounds") or [],                       # missing on replies from before this report existed
            "tools": [c.get("name", "") for c in calls],
            "tool_usd": meta.get("tool_usd") or []})
    return out
