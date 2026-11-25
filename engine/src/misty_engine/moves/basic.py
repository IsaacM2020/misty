"""The first three moves, all safe: open an app, open a web link, open a file or folder."""
from __future__ import annotations

import os
import re

from ..errors import Fatal, MistyError
from .base import Move, Outcome, Param
from .registry import register


async def _open_app(ctx, p) -> Outcome:
    hands = (ctx.extras or {}).get("hands")
    if hands is None or not getattr(hands, "connected", False):
        return Outcome(False, what="Misty's hands aren't connected, so she can't open apps right now")
    try:
        info = await hands._call("open_app", name=p["name"])
    except (Fatal, MistyError) as e:
        return Outcome(False, what=f"couldn't open {p['name']} ({e})")
    return Outcome(True, what=f"opened {info.get('name') or p['name']}", data={"pid": info.get("pid")})


async def _app_running(ctx, p, out) -> bool:
    hands = (ctx.extras or {}).get("hands")
    try:
        wins = (await hands._call("window_list")).get("windows") or []
    except (Fatal, MistyError):
        return False
    want = (out.data.get("pid") or -1)
    return any(w.get("pid") == want for w in wins) or any(p["name"].lower() in str(w.get("app", "")).lower() for w in wins)


PAYMENT = re.compile(r"(?i)/(?:checkout|payment|pay|billing)(?:[/?#]|$)")


async def _open_url(ctx, p) -> Outcome:
    from .. import chat
    url = p["url"]
    if not re.fullmatch(r"https://[^\s]{4,500}", url):
        return Outcome(False, what="only https links can be opened")
    if PAYMENT.search(url):
        return Outcome(False, what="that looks like a payment page, so she doesn't open it on her own")
    await chat._open(url)
    return Outcome(True, what=f"opened {url[:80]}", data={"url": url})


async def _opened(ctx, p, out) -> bool:
    return True            # `open` exited 0 (a failure raises): the system accepted the link. There is no deeper read-back for a browser.


async def _open_path(ctx, p) -> Outcome:
    from .. import brief, chat
    path = os.path.expanduser(p["path"])
    folders = ctx.settings.get("folders") or []
    if not brief.safe_target({"kind": "folder", "target": path}, folders):
        return Outcome(False, what="that isn't inside the folders he has shared with her")
    if not os.path.exists(path):
        return Outcome(False, what="that folder isn't there")
    await chat._open(path)
    return Outcome(True, what=f"opened {os.path.basename(path.rstrip('/')) or path}", data={"path": path})


async def _path_exists(ctx, p, out) -> bool:
    return os.path.exists(out.data["path"])


register(Move("open_app", "open an app by name", {"name": Param(str, max_len=60, doc="the app's name")}, "safe", _open_app, _app_running,
              says=("open Preview", "launch Spotify", "bring up the Calculator", "start up Keynote"),
              not_mine=(("click the blue button in Notion", "do"), ("what's on my screen right now", "look"))))
register(Move("open_url", "open an https link in the browser", {"url": Param(str, max_len=500, doc="https link")}, "safe", _open_url, _opened,
              says=("open this link for me https://example.com/page", "go to https://www.khanacademy.org", "pull up https://en.wikipedia.org/wiki/Mitosis"),
              not_mine=(("what does this page say https://example.com/page", "read_page"), ("search the web for mitosis diagrams", "search"))))
register(Move("open_path", "open a folder inside the folders he shared", {"path": Param(str, max_len=300)}, "safe", _open_path, _path_exists,
              says=("open my Math folder", "show me my chemistry folder", "open the folder with my physics notes"),
              not_mine=(("find the file I was working on yesterday", "recent_files"), ("open my class notes from Tuesday", "class_notes"))))


# ---- dictation by talking (ambient voice): put his words where his cursor is -------------------------------------------
MISTY_BUNDLE = "com.isaacmiranda.Misty"


async def _write_here(ctx, p) -> Outcome:
    """Types the text into the app he is in, at the cursor. One line only: a typed Return could send a message or submit a
    prompt, and this move never sends anything. If Misty herself is in front (her pill or chat took focus), it goes back to the
    app he was in before. A private app is refused."""
    import asyncio
    from .. import privacy
    hands = (ctx.extras or {}).get("hands")
    if hands is None or not getattr(hands, "connected", False):
        return Outcome(False, what="Misty's hands aren't connected, so she can't type right now")
    text = " ".join(str(p["text"]).split())
    if not text:
        return Outcome(False, what="there was nothing to write")
    try:
        front = await hands._call("front_app")
    except (Fatal, MistyError):
        front = {}
    if not front or front.get("bundle_id") == MISTY_BUNDLE:
        try:
            front = await hands._call("last_app")
            await hands._call("open_app", name=front["name"])
            await asyncio.sleep(0.3)
        except (Fatal, MistyError, KeyError, TypeError):
            return Outcome(False, what="there's no app open to write into")
    name = str(front.get("name") or "the app")
    if front.get("bundle_id") in set(privacy.bundles(ctx)):
        return Outcome(False, what=f"{name} is one of the apps she doesn't touch")
    try:
        await hands._call("type_text", text=text)
    except (Fatal, MistyError) as e:
        return Outcome(False, what=f"couldn't type into {name} ({e})")
    return Outcome(True, what=f"wrote it into {name}, not sent", data={"app": name, "chars": len(text)})


async def _typed(ctx, p, out) -> bool:
    return bool(out.data.get("chars"))          # the keystrokes were posted; the app's own field is not read back


register(Move("write_here", "type his words into the app he is in, at the cursor (an email reply, a message, a prompt, a doc). "
              "text is what he meant to write, cleaned up: no filler, his meaning and words, nothing added. Never sends or submits. "
              "Call it straight away when he says write, type, put in, reply or tell them: no need to read the screen first, it types where his cursor is",
              {"text": Param(str, max_len=4000, doc="the words to write, as he meant them")}, "safe", _write_here, _typed,
              no_text=True,
              says=("reply saying Thursday works for me", "write: hey, thanks for getting back to me", "type that I'll be late by ten minutes",
                    "put this in: make the three classifiers one model"),
              not_mine=(("send Mum a message saying I'm on my way", "move"), ("what's on my screen right now", "look"))))
