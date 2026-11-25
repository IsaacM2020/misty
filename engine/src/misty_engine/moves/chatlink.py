"""Isaac (16 Nov 2026): "launch Shipra class" = find the class link the tutor sends in a WhatsApp group that day, open it.

`open_chat_link {chat, today_only?, browser?}`: the newest https link in one WhatsApp chat (groups are fine: this only
reads), opened in his browser. Read-only on WhatsApp, local only (ChatStorage.sqlite, the same file the WhatsApp source
reads). Payment pages are never opened. Only the link's site goes into the log and the reply, never the full link."""
from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from ..clock import SGT
from .base import Move, Outcome, Param
from .registry import register

WA_DB = Path.home() / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite"
APPLE_EPOCH = 978307200
URL = re.compile(r"https://[^\s<>\"')\]]+")
PAYMENT = re.compile(r"(?i)/(?:checkout|payment|pay|billing)(?:[/?#]|$)")
NUMS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
        "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13"}
SHORT = {"grade": "gr", "maths": "math", "mathematics": "math", "secondary": "sec", "physics": "phy", "chemistry": "chem",
         "biology": "bio", "economics": "econ", "english": "eng"}
FILLER = {"the", "group", "chat", "whatsapp", "on", "in", "my", "a", "class"}
BROWSERS = {"safari": "Safari", "chrome": "Google Chrome"}


def _words(s: str) -> list[str]:
    ws = re.sub(r"[^\w ]", " ", (s or "").lower()).split()
    return [SHORT.get(w, NUMS.get(w, w)) for w in ws]          # both sides: "Grade 11 Maths" and "Gr 11 Math" match


def name_fits(said: str, name: str) -> bool:
    """Every real word he said starts a word of the chat's name ("grade eleven shipra" fits "Gr 11 Maths - Shipra")."""
    want = [w for w in _words(said) if w not in FILLER] or _words(said)
    have = _words(name)
    return bool(want) and all(any(h.startswith(w) or (w.isdigit() and h == w) for h in have) for w in want)


def _connect(path: Path = None) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path or WA_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def find(said: str, path: Path | None = None) -> tuple[list[dict], dict | None]:
    """(the chats whose name fits, the newest https link in the one chat that fits) - link is None unless exactly one fits."""
    con = _connect(path)
    try:
        chats = [{"pk": r["Z_PK"], "name": r["ZPARTNERNAME"] or ""} for r in con.execute("SELECT Z_PK, ZPARTNERNAME FROM ZWACHATSESSION")
                 if r["ZPARTNERNAME"] and name_fits(said, r["ZPARTNERNAME"])]
        if len(chats) != 1:
            return chats, None
        for r in con.execute("SELECT ZTEXT, ZMESSAGEDATE FROM ZWAMESSAGE WHERE ZCHATSESSION=? AND ZTEXT LIKE '%https://%' "
                             "ORDER BY ZMESSAGEDATE DESC, Z_PK DESC LIMIT 20", (chats[0]["pk"],)):
            urls = URL.findall(r["ZTEXT"] or "")
            if urls:
                return chats, {"url": urls[-1].rstrip(".,;:!"), "at": float(r["ZMESSAGEDATE"] or 0) + APPLE_EPOCH}
        return chats, {}
    finally:
        con.close()


def site(url: str) -> str:
    m = re.match(r"https://([^/?#]+)", url)
    return m[1] if m else "a link"


async def _exec(*argv: str, timeout: float = 20.0) -> int:
    """Tests replace this: no test ever opens a link."""
    p = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        return await asyncio.wait_for(p.wait(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return 124


async def _open_link(ctx, p) -> Outcome:
    try:
        chats, link = await asyncio.to_thread(find, p["chat"])
    except sqlite3.Error:
        return Outcome(False, what="she can't read WhatsApp right now (it needs Full Disk Access, and WhatsApp must be installed)")
    if not chats:
        return Outcome(False, what=f"there is no WhatsApp chat called {p['chat']}: ask him for the group's name")
    if len(chats) > 1:
        names = ", ".join(c["name"] for c in chats[:5])
        return Outcome(False, what=f"more than one WhatsApp chat fits {p['chat']}: {names}. Ask him which one")
    name = chats[0]["name"]
    if not link:
        return Outcome(False, what=f"no link has been sent in {name}")
    sent = datetime.fromtimestamp(link["at"], SGT)
    today = datetime.fromtimestamp(ctx.clock.now(), SGT).date()
    if p.get("today_only", True) and sent.date() != today:
        return Outcome(False, what=f"no link was sent in {name} today; the newest one ({site(link['url'])}) is from {sent:%a %-d %b}. "
                                   "Nothing was opened")
    if PAYMENT.search(link["url"]):
        return Outcome(False, what="the newest link there looks like a payment page, so she doesn't open it on her own")
    app = BROWSERS.get((p.get("browser") or "").lower())
    code = await _exec(*(["open", "-a", app] if app else ["open"]), link["url"])
    if code != 0:
        return Outcome(False, what=f"the link wouldn't open (exit {code})")
    return Outcome(True, what=f"opened the newest link from {name} ({site(link['url'])}, sent {sent:%-I:%M %p})" + (f" in {app}" if app else ""),
                   data={"exit": code, "site": site(link["url"])})


async def _opened(ctx, p, out) -> bool:
    return out.data.get("exit") == 0            # the system took the link; a browser gives no deeper read-back


register(Move("open_chat_link", "open the newest https link sent in one WhatsApp chat or group (default: only if it was sent today); "
              "browser safari or chrome, else his default",
              {"chat": Param(str, max_len=80, doc="the chat or group's name"), "today_only": Param(bool, required=False),
               "browser": Param(str, required=False, choices=("safari", "chrome", "default"))},
              "safe", _open_link, _opened,
              says=("open the class link from the grade eleven maths group", "open the latest link in the Shipra tuition chat",
                    "open today's zoom link from my physics group in safari"),
              not_mine=(("open this link for me https://example.com/page", "move"), ("what did the maths group say today", "recall"))))
