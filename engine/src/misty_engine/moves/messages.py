"""D5: messages, the way Isaac chose in D0 (16 Nov 2026). One person, never a group.

- message_imessage: `ask`. On his yes, sent through AppleScript; proved by finding it in the Messages database within 10 s.
- message_whatsapp: `safe`. Opens the chat with the text filled in. He presses Enter. She never says it was sent.
- mail_draft: `safe`. A Gmail draft (never sent), opened for him; proved by reading the draft back. Sending is his click."""
from __future__ import annotations

import asyncio
import base64
import re
import sqlite3
import time
import urllib.parse
from email.message import EmailMessage
from pathlib import Path

from ..sources import google_auth
from . import people
from .base import Move, Outcome, Param
from .registry import register

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
VERIFY_S = 10.0

SEND_SCRIPT = '''on run argv
  set theHandle to item 1 of argv
  set theText to item 2 of argv
  tell application "Messages"
    set svc to 1st account whose service type = iMessage
    send theText to participant theHandle of svc
  end tell
end run'''


async def _exec(*argv: str, timeout: float = 30.0) -> tuple[int, str]:
    """Runs a command, returns (exit code, output). Tests replace this: no test ever sends a message or opens an app."""
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timed out"
    return proc.returncode or 0, out.decode(errors="replace")[:300]


def sent_texts(handle: str, since: float) -> list[str]:
    """What he (or she, for him) sent to this handle since `since`, newest first, read-only from chat.db."""
    from ..sources.typedstream import decode_attributed_body as decode
    apple = int((since - 978307200) * 1e9)
    con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT m.text, m.attributedBody FROM message m JOIN handle h ON h.ROWID = m.handle_id "
                           "WHERE m.is_from_me = 1 AND h.id = ? AND m.date >= ? ORDER BY m.ROWID DESC LIMIT 10", (handle, apple)).fetchall()
    finally:
        con.close()
    return [t or decode(b) or "" for t, b in rows]


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


# ---- iMessage

def _imessage_preview(p, ctx) -> str:
    who, _ = people.pick(ctx, p["to"], "imessage")
    return f"Send to {who['name'] if who else p['to']} on iMessage: \"{p['text']}\"?"


_imessage_preview.wants_ctx = True


async def _imessage(ctx, p) -> Outcome:
    who, why = people.pick(ctx, p["to"], "imessage")
    if who is None:
        return Outcome(False, what=why)
    t0 = time.time()
    code, out = await _exec("osascript", "-e", SEND_SCRIPT, who["handle"], p["text"])
    if code != 0:
        return Outcome(False, what="Messages wouldn't send it" + (" (macOS may be asking to let Misty control Messages: he should click OK)"
                                                                  if "not allowed" in out.lower() or "-1743" in out else f" (exit {code})"))
    return Outcome(True, what=f"sent to {who['name']} on iMessage", data={"handle": who["handle"], "since": t0 - 5, "name": who["name"]})


async def _imessage_there(ctx, p, out) -> bool:
    end = time.monotonic() + VERIFY_S
    while True:
        try:
            if any(_same(t, p["text"]) for t in await asyncio.to_thread(sent_texts, out.data["handle"], out.data["since"])):
                return True
        except sqlite3.Error:
            return False
        if time.monotonic() >= end:
            return False
        await asyncio.sleep(1.0)


# ---- WhatsApp (filled in, he presses Enter)

async def _whatsapp(ctx, p) -> Outcome:
    who, why = people.pick(ctx, p["to"], "whatsapp")
    if who is None:
        return Outcome(False, what=why)
    url = "whatsapp://send?" + urllib.parse.urlencode({"phone": who["handle"].lstrip("+"), "text": p["text"]})
    code, _ = await _exec("open", url)
    if code != 0:
        return Outcome(False, what="WhatsApp didn't open (is the WhatsApp app installed?)")
    return Outcome(True, what=f"filled in a WhatsApp message to {who['name']}, NOT sent: he presses Enter to send it",
                   data={"exit": code, "sent": False})


async def _opened(ctx, p, out) -> bool:
    return out.data.get("exit") == 0          # the system took the link; WhatsApp gives no read-back of a filled box


# ---- mail draft (never sent)

_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")


def _gmail():
    return google_auth.service("gmail")


async def _draft(ctx, p) -> Outcome:
    if google_auth.MAIL_DRAFT not in google_auth.granted():
        return Outcome(False, what="she can't make mail drafts yet: he needs to sign in to Google once more (EDITH Settings, Google, "
                                   "sign in again). Reading his mail still works")
    to, name = p["to"].strip(), p["to"].strip()
    if not _EMAIL.match(to):
        who, why = people.pick(ctx, to, "mail")
        if who is None:
            return Outcome(False, what=why + ". Or he can give the email address")
        to, name = who["handle"], who["name"]
    msg = EmailMessage()
    msg["To"], msg["Subject"] = to, p["subject"]
    msg.set_content(p["body"])
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def go():
        return _gmail().users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    d = await asyncio.to_thread(go)
    mid = (d.get("message") or {}).get("id", "")
    try:
        from .. import chat
        await chat._open(f"https://mail.google.com/mail/u/0/#drafts?compose={mid}")
    except Exception:
        pass                                   # the draft exists either way; opening it is a courtesy
    return Outcome(True, what=f"drafted a mail to {name}, NOT sent: it's in his Gmail drafts for him to send",
                   data={"draft_id": d["id"], "sent": False})


async def _draft_there(ctx, p, out) -> bool:
    def go():
        d = _gmail().users().drafts().get(userId="me", id=out.data["draft_id"]).execute()
        return bool(d.get("id"))
    try:
        return await asyncio.to_thread(go)
    except Exception:
        return False


register(Move("message_imessage", "send one iMessage to one person (by name), on his yes. Never a group",
              {"to": Param(str, max_len=80, doc="the person's name"), "text": Param(str, max_len=1000)},
              "ask", _imessage, _imessage_there, preview=_imessage_preview, no_line="Okay, not sent.",
              says=("text my mum on iMessage that I'll be home by 7", "imessage Shipra I'll be late",
                    "send dad a message saying I'm at the library"),
              not_mine=(("what did Shipra text me yesterday", "recall"), ("put the Chem test in my calendar for Friday", "move"))))

register(Move("message_whatsapp", "open WhatsApp with a message to one person filled in; he presses Enter (she never sends it)",
              {"to": Param(str, max_len=80, doc="the person's name"), "text": Param(str, max_len=1000)},
              "safe", _whatsapp, _opened,
              says=("whatsapp Aarav that practice is moved to 5", "tell Shipra on WhatsApp I'll be late",
                    "message Rohan on whatsapp asking if he has the physics notes"),
              not_mine=(("what's the latest in my WhatsApp chats", "recall"), ("open WhatsApp", "move"))))

register(Move("mail_draft", "write a Gmail draft to one person (name or email) and open it; it is never sent, he sends it",
              {"to": Param(str, max_len=120), "subject": Param(str, max_len=150), "body": Param(str, max_len=4000)},
              "safe", _draft, _draft_there,
              says=("draft an email to Mr Tan asking for an extension on the IA", "write an email to my coach saying I can't make Saturday",
                    "can you draft a mail to Ms Lim about the chem test date"),
              not_mine=(("did Mr Tan email me back", "mail"), ("what emails came in today", "mail"))))
