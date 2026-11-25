"""D5: who "Shipra" is, and how he usually talks to her. Local only: his 1:1 chats she already reads (iMessage, WhatsApp)
and the people registry's handles. Handles never go into a prompt or a log: only names and channels go back to the model."""
from __future__ import annotations

import json
import re

CHANNELS = ("imessage", "whatsapp", "mail")


def _words(s: str) -> list[str]:
    return [w for w in re.sub(r"[^\w@.+ ]", " ", (s or "").lower()).split() if w]


def name_matches(who: str, name: str) -> bool:
    """Every word he said starts a word of the name: "shipra" fits "Shipra Kumar", "ms tan" fits nothing called "Tan Wei"."""
    want, have = _words(who), _words(name)
    return bool(want) and all(any(h.startswith(w) for h in have) for w in want)


def _ok_handle(kind: str, value: str) -> bool:
    if kind == "phone":
        return bool(re.fullmatch(r"\+\d{7,15}", value or ""))
    return kind == "email" and "@" in (value or "")


def resolve(ctx, who: str) -> list[dict]:
    """[{name, channel, handle, last}] for every person whose name fits `who`, one row per channel, newest first.
    `last` is the week he last talked to them on that channel, as 202646 (0 when only the registry knows the handle)."""
    who = " ".join((who or "").split())
    if not who:
        return []
    rows: dict[tuple[str, str], dict] = {}

    def offer(name: str, channel: str, handle: str, last: float) -> None:
        k = (name.lower(), channel)
        if k not in rows or last > rows[k]["last"]:
            rows[k] = {"name": name, "channel": channel, "handle": handle, "last": last}

    q = ("SELECT source, json_extract(meta, '$.week') AS wk, json_extract(meta, '$.counterpart') AS cp FROM items "
         "WHERE source IN ('imessage', 'whatsapp') AND json_extract(meta, '$.counterpart') IS NOT NULL")
    for r in ctx.con.execute(q):
        try:
            cp = json.loads(r["cp"])
        except (TypeError, ValueError):
            continue
        h = cp.get("handle") or []
        if cp.get("name") and len(h) == 2 and _ok_handle(h[0], h[1]) and name_matches(who, cp["name"]):
            m = re.fullmatch(r"(\d{4})-W(\d{2})", r["wk"] or "")
            offer(cp["name"], r["source"], h[1], float(int(m[1]) * 100 + int(m[2])) if m else 0.0)
            if h[0] == "email":                           # an iMessage email address is a mail address too
                offer(cp["name"], "mail", h[1], 0.0)
    try:
        from ..registry.lookup import lookup
        found = lookup(ctx, who, "person")["candidates"]
    except Exception:
        found = []
    for c in found:
        if c["confidence"] < 0.6 or not name_matches(who, c["name"]):
            continue
        for h in c["facts"].get("handles") or []:
            if not _ok_handle(h["type"], h["value"]):
                continue
            for ch in (("imessage", "whatsapp") if h["type"] == "phone" else ("mail", "imessage")):
                offer(c["name"], ch, h["value"], 0.0)
    return sorted(rows.values(), key=lambda r: (-r["last"], r["name"], r["channel"]))


def people(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for r in rows:
        if r["name"] not in out:
            out.append(r["name"])
    return out


def pick(ctx, who: str, channel: str) -> tuple[dict | None, str]:
    """(the one person on this channel, "") or (None, what to say: ask which person, or which channel they use)."""
    if re.search(r",|&|\band\b|\bgroup\b|\beveryone\b|\ball\b", who or "", re.I):
        return None, "she messages one person at a time, never a group"
    rows = resolve(ctx, who)
    names = people(rows)
    if not names:
        return None, f"she doesn't know who {who} is or how to reach them: ask him for their full name"
    if len(names) > 1:
        return None, f"more than one person fits {who}: {', '.join(names[:5])}. Ask him which one"
    on = [r for r in rows if r["channel"] == channel]
    if not on:
        others = sorted({r["channel"] for r in rows}, key=CHANNELS.index)
        return None, f"{names[0]} isn't reachable on {channel}; they are on {', '.join(others)}: use that move instead"
    return on[0], ""


def usual(ctx, who: str) -> tuple[str | None, str | None]:
    """(person, the chat channel he used with them most recently) or (None, None). Mail only when there is no chat."""
    rows = resolve(ctx, who)
    if len(people(rows)) != 1:
        return None, None
    chats = [r for r in rows if r["channel"] != "mail"]
    best = (chats or rows)[0]
    return best["name"], best["channel"]
