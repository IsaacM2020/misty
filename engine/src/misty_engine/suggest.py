"""Suggestion cards. Jev's live triage says a card is wanted (kind, urgency); the text is the one-line `do`
Haiku writes while compiling the note. No `do` means no card."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .clock import SGT, Clock
from .types import new_id

CARD_MAX = 140
URGENCIES = ("now", "today", "this_week", "none")
CARD_ITEM_KINDS = ("mail_thread", "calendar_week", "chat_week")   # never files/repos: edits to his own docs are not asks
PERSON_RE = re.compile(r"\+?\d{8,15}")
OTP_RE = re.compile(r"\b(otp|one[- ]time (pass(word|code)?|code|pin)|verification code|security code|"
                    r"passcode|login code|is your [a-z ]{0,20}code)\b", re.I)


def chat_handle(meta: dict) -> str:
    """'any;-;+6591234567' -> '+6591234567'; group chats ('any;+;<guid>') -> ''."""
    cid = meta.get("chat_id") or ""
    return "" if ";+;" in cid else cid.rsplit(";", 1)[-1]


def card_allowed(item_kind: str, meta: dict, excerpt: str) -> bool:
    """Cards only from mail, calendar and chats with people. Business sender IDs (Singtel, Carousell, 611123-P)
    and one-time codes never make cards; their notes are still written."""
    if item_kind not in CARD_ITEM_KINDS:
        return False
    if item_kind == "chat_week":                         # mail OTPs/receipts get do="" from Haiku instead
        h = chat_handle(meta)
        if h and "@" not in h and not PERSON_RE.fullmatch(h):
            return False
        return not OTP_RE.search(excerpt or "")
    return True


def place_card(con, item_id: str, kind: str, text: str, urgency: str, sender: str | None,
               clock: Clock | None = None) -> str | None:
    """New card, or a fresher `do` for the open card of a chat week / thread that got new messages."""
    sid = add(con, item_id, kind, text, urgency, sender, clock)
    if sid is None:
        con.execute("UPDATE suggestions SET text=?, updated_at=? WHERE item_id=? AND kind=? AND status='new'",
                    (_clip(text, CARD_MAX), (clock or Clock()).now(), item_id, kind))
    return sid


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def card_text(kind: str, title: str, sender: str | None = None, n: int = 0, m: int = 0) -> str:
    who = sender or "sender"
    templates = {
        "reply": f"Reply to {who}: {title}",
        "calendar": f"Add to calendar: {title}",
        "task": f"To do: {title}",
        "fact": f"Remember: {title}",
        "mute": f"Mute {who}? You dismissed {n} of {m} cards",
    }
    return _clip(templates[kind], CARD_MAX)


def add(con, item_id: str | None, kind: str, text: str, urgency: str, sender: str | None,
        clock: Clock | None = None) -> str | None:
    clock = clock or Clock()
    if urgency not in URGENCIES:
        urgency = "none"
    if item_id is not None and con.execute("SELECT 1 FROM suggestions WHERE item_id=? AND kind=?",
                                           (item_id, kind)).fetchone():
        return None
    sid = new_id("sg")
    now = clock.now()
    con.execute("INSERT INTO suggestions(id, item_id, kind, text, urgency, status, sender, created_at, updated_at) "
                "VALUES (?,?,?,?,?, 'new', ?, ?, ?)", (sid, item_id, kind, _clip(text, CARD_MAX), urgency, sender, now, now))
    return sid


def _row(r) -> dict:
    return {k: r[k] for k in r.keys()}


def release_snoozes(con, clock: Clock) -> int:
    cur = con.execute("UPDATE suggestions SET status='new', snooze_until=NULL, updated_at=? "
                      "WHERE status='snoozed' AND snooze_until<=?", (clock.now(), clock.now()))
    return cur.rowcount


def list_cards(con, status: str = "new", clock: Clock | None = None) -> list[dict]:
    if clock is not None:
        release_snoozes(con, clock)
    rows = con.execute(
        "SELECT s.*, i.locator AS locator, i.title AS title, i.source AS source FROM suggestions s "
        "LEFT JOIN items i ON i.id = s.item_id WHERE s.status=? ORDER BY s.created_at DESC, s.rowid DESC",
        (status,)).fetchall()
    return [_row(r) for r in rows]


def _snooze_until(until: str, clock: Clock) -> float:
    now = clock.now()
    if until == "1h":
        return now + 3600
    d = datetime.fromtimestamp(now, SGT)
    if until == "tonight":
        t = d.replace(hour=20, minute=0, second=0, microsecond=0)
        return t.timestamp() if t.timestamp() > now else now + 3600
    if until == "tomorrow":
        t = (d + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        return t.timestamp()
    raise ValueError("UNTIL")


def act(con, sid: str, action: str, until: str | None, clock: Clock) -> None:
    now = clock.now()
    if action == "done":
        con.execute("UPDATE suggestions SET status='done', updated_at=? WHERE id=?", (now, sid))
    elif action == "dismiss":
        con.execute("UPDATE suggestions SET status='dismissed', updated_at=? WHERE id=?", (now, sid))
    elif action == "snooze":
        con.execute("UPDATE suggestions SET status='snoozed', snooze_until=?, updated_at=? WHERE id=?",
                    (_snooze_until(until or "1h", clock), now, sid))
    else:
        raise ValueError("ACTION")


def badge(con) -> bool:
    return con.execute("SELECT 1 FROM suggestions WHERE status='new' AND urgency='now' LIMIT 1").fetchone() is not None


def weekly_mutes(con, clock: Clock) -> list[str]:
    since = clock.now() - 28 * 86400
    rows = con.execute(
        "SELECT sender, COUNT(*) n, SUM(status='dismissed') d FROM suggestions "
        "WHERE sender IS NOT NULL AND kind != 'mute' AND created_at>=? GROUP BY sender", (since,)).fetchall()
    out = []
    for r in rows:
        if r["n"] >= 5 and r["d"] / r["n"] >= 0.8:
            open_ = con.execute("SELECT 1 FROM suggestions WHERE kind='mute' AND sender=? AND status IN ('new','snoozed')",
                                (r["sender"],)).fetchone()
            if not open_:
                add(con, None, "mute", card_text("mute", "", r["sender"], r["d"], r["n"]), "none", r["sender"], clock)
                out.append(r["sender"])
    return out
