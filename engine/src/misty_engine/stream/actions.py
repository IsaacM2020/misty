"""Stream's hands: the actions a brain may ask for, and how each one is done on the Mac.

Order of preference: the app's own machinery (menu, AXPress, set value) first. A real mouse click or key press only when
that fails, because raw input needs the app in front and would fight Isaac's own mouse.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from dataclasses import dataclass, field

from ..errors import Fatal
from . import gate
from .bridge import AppInfo
from .observe import Observation, name_of

ACTIONS = ("press", "type_into", "type_keys", "menu", "shortcut", "open_app", "open_url", "scroll", "look", "wait", "list_files", "find_files", "open_path", "clear_desktop", "ask_user", "done")

TOOL = {"type": "function", "function": {
    "name": "act", "description": "Do exactly one action on the Mac.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "id": {"type": "integer", "description": "press, type_into, scroll: the [number] of the control"},
        "ids": {"type": "array", "items": {"type": "integer"}, "description": "press: several [numbers] pressed in order, e.g. keypad buttons"},
        "text": {"type": "string", "description": "type_into / type_keys: the text. ask_user: the question. find_files: the words in the file's name"},
        "submit": {"type": "boolean", "description": "type_into: press return after typing"},
        "replace": {"type": "boolean", "description": "type_into a text area: replace everything instead of adding at the end"},
        "path": {"type": "array", "items": {"type": "string"}, "description": "menu: e.g. [\"File\", \"New Note\"]"},
        "keys": {"type": "string", "description": "shortcut: e.g. 'cmd+t', 'return', 'escape'"},
        "name": {"type": "string", "description": "open_app: the app's name, e.g. 'Notes'"},
        "url": {"type": "string", "description": "open_url: a full https:// URL"},
        "direction": {"type": "string", "enum": ["up", "down"]},
        "folder": {"type": "string", "description": "list_files, find_files: e.g. 'Downloads', 'Desktop', 'Documents' or a full path"},
        "sort": {"type": "string", "enum": ["added", "modified", "name"], "description": "list_files: newest first by date added (default) or modified"},
        "kind": {"type": "string", "description": "list_files: optional file extension filter, e.g. 'pdf'"},
        "file": {"type": "string", "description": "open_path: full path of a file or folder (from list_files or find_files) to open in its default app"},
        "evidence": {"type": "string", "description": "done: what on screen proves the task is finished"},
        "thought": {"type": "string", "description": "one short line: why this action"}},
        "required": ["action"]}}}


@dataclass
class Outcome:
    text: str                                   # what happened, in one line for the history
    app: AppInfo | None = None                  # the app Stream is now working in, if it changed
    image: bytes | None = None                  # a screenshot the brain asked for
    stop: str | None = None                     # the run must end: never, denied, error
    risky: str = ""                             # what the gate asked about, if it did
    raw: bool = False                           # real mouse or keyboard input was used
    data: dict = field(default_factory=dict)


Confirm = "Callable[[str], Awaitable[bool]]"


def describe(a: dict, obs: Observation) -> str:
    """'press "Send"': what Isaac reads in a confirm card."""
    k = a.get("action")
    if k in ("press", "type_into", "scroll"):
        e = obs.get(a.get("id"))
        what = f'"{name_of(e.node)}"' if e else f"[{a.get('id')}]"
        return f"{k} {what}" + (f' with "{a.get("text", "")[:60]}"' if k == "type_into" else "")
    if k == "menu":
        return "menu " + " > ".join(a.get("path") or [])
    if k == "shortcut":
        return f"shortcut {a.get('keys')}"
    return f"{k} {a.get('name') or a.get('url') or ''}".strip()


def _folder(name: str) -> Path:
    p = Path(os.path.expanduser(name.strip() or "~/Downloads"))
    if not p.is_absolute():
        p = Path.home() / name.strip().strip("/")
    return p


def list_files(folder: str, sort: str = "added", kind: str = "", n: int = 12) -> str:
    """The folder's newest files as plain lines with full paths: a hack that beats sorting Finder by hand."""
    d = _folder(folder)
    if not d.is_dir():
        return f"no folder {d}"
    fs = [p for p in d.iterdir() if not p.name.startswith(".") and (not kind or p.suffix.lower() == "." + kind.lower().lstrip("."))]
    if sort == "name":
        fs.sort(key=lambda p: p.name.lower())
    elif sort == "modified":
        fs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        out = subprocess.run(["mdls", "-name", "kMDItemDateAdded", "-raw", *map(str, fs)], capture_output=True, text=True).stdout
        added = out.split("\0") if fs else []
        stamp = {f: (added[i] if i < len(added) and added[i] != "(null)" else "") for i, f in enumerate(fs)}
        fs.sort(key=lambda p: stamp[p], reverse=True)
    lines = [f"{p}" + ("/" if p.is_dir() else "") for p in fs[:n]]
    return f"{len(fs)} items in {d}, {'newest first' if sort != 'name' else 'by name'}:\n" + "\n".join(lines)


FRONT_WAIT = 3.0        # after opening a file: how long to wait for another app to come to the front
FIND_MAX = 10


def _mdfind(folder: str, word: str) -> list[str]:
    """Spotlight, by file name, inside one folder. Fast (an index, not a walk of the disk)."""
    out = subprocess.run(["mdfind", "-onlyin", folder, "-name", word], capture_output=True, text=True, timeout=8)
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def find_files(folder: str, name: str) -> str:
    """V5: files whose name has all these words, newest first, up to 10 full paths, only inside his home. Hidden
    folders and ~/Library are refused (keys, mail, app data), so a name search can't wander into them."""
    words = [w for w in name.lower().split() if w]
    if not words:
        return "find_files needs the words in the file's name, for example text 'functions'"
    home = Path(os.path.expanduser("~"))
    d = Path(os.path.normpath(_folder(folder or "~")))
    try:
        rel = d.relative_to(home)
    except ValueError:
        return f"refused: find_files only looks inside your home folder, not {d}"
    if any(part.startswith(".") for part in rel.parts) or (rel.parts and rel.parts[0] == "Library"):
        return f"refused: {d} is a private place, find_files does not look there"
    if not d.is_dir():
        return f"no folder {d}"
    hits: dict[str, float] = {}
    for line in _mdfind(str(d), max(words, key=len)):
        p = Path(line)
        try:
            sub = p.relative_to(d)
        except ValueError:
            continue
        if any(part.startswith(".") for part in sub.parts) or "Library" in p.relative_to(home).parts[:1]:
            continue
        if all(w in p.name.lower() for w in words):
            try:
                hits[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    if not hits:
        return f"no file named like '{name}' in {d}"
    ordered = sorted(hits, key=lambda x: hits[x], reverse=True)
    n = len(ordered)
    head = f"{n} files named like '{name}' in {d}, newest first:" if n <= FIND_MAX else \
        f"{FIND_MAX} of {n} files named like '{name}' in {d}, newest first:"
    return head + "\n" + "\n".join(ordered[:FIND_MAX])


async def _wait_front(hands, before_pid: int):
    """After opening a file: wait up to FRONT_WAIT for another app to come front. (app, changed)."""
    import time
    end = time.monotonic() + FRONT_WAIT
    while True:
        front = await hands.front_app()
        if front.pid != before_pid:
            await asyncio.sleep(0.4)                    # let its window draw
            return await hands.front_app(), True
        if time.monotonic() >= end:
            return front, False
        await asyncio.sleep(0.25)


MISTY_BUNDLE = "com.isaacmiranda.Misty"


async def clear_desktop(hands) -> Outcome:
    """Minimise every open window of every app (Misty's own stay): one action instead of hunting for each app's
    Window > Minimize All, which a brain once got wrong 20 times in a row. Nothing is closed or quit."""
    done, full = 0, []
    for w in await hands.window_list():
        if w.minimised or w.bundle_id == MISTY_BUNDLE:
            continue
        if w.fullscreen:                                # macOS can't minimise a full-screen window
            full.append(w.app)
            continue
        if await hands.set_minimised(w.pid, w.index, True):
            done += 1
    text = f"minimised {done} windows"
    if full:
        text += f"; full screen (left as is): {', '.join(sorted(set(full)))}"
    return Outcome(text)


async def _shows(hands, app: AppInfo, e, text: str) -> bool:
    """After a silent set value: does a control of the same kind now hold the text?"""
    from .observe import LIST_PREVIEW, MAX_NODES
    nodes = await hands.ax_snapshot(app.pid, MAX_NODES, LIST_PREVIEW)
    want = text.strip()[:40]
    return any(n.role == e.node.role and want in (n.value or "") for n in nodes)


async def paste(hands, text: str) -> None:
    """Type by pasting: synthetic per-character key events don't reach SwiftUI fields or launchers (cu-spike, Notes
    search). The clipboard is put back right after."""
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode(), check=False)
    await asyncio.sleep(0.05)
    await hands.keys("cmd+v")
    await asyncio.sleep(0.3)
    subprocess.run(["pbcopy"], input=old, check=False)


async def _front(hands, app: AppInfo) -> None:
    """Raw keys and clicks go to whatever app is in front, so Stream's app must really be in front first.

    macOS ignores activate() from a background app (that is how pasted text landed in Finder and a web chat on
    2026-10-21), so this asks LaunchServices via `open`, then checks. If the app still isn't in front, the raw action
    is refused: no key or click is sent anywhere else.
    """
    for attempt in range(8):
        if (await hands.front_app()).pid == app.pid:
            return
        if attempt in (0, 4):
            cmd = ["open", "-b", app.bundle_id] if app.bundle_id else ["open", "-a", app.name]
            subprocess.run(cmd, check=False, capture_output=True)
        await asyncio.sleep(0.25)
    raise Fatal("NOT_FRONT")


async def show(hands, do: str, frame=None, label: str = "") -> float:
    """Stream's own cursor on screen. Only a picture, so it never fails a step (fake or older hands lack it)."""
    if not hasattr(hands, "cursor"):
        return 0.0
    try:
        return await hands.cursor(do, frame, label)
    except Exception:
        return 0.0


async def _aim(hands, e) -> None:
    """Glide the cursor to the control, then pulse just as it is pressed."""
    secs = await show(hands, "aim", e.node.frame, name_of(e.node)[:40])
    if secs:
        await asyncio.sleep(min(secs, 0.9))
    await show(hands, "click")


async def run(a: dict, obs: Observation, hands, confirm) -> Outcome:
    k, app = a.get("action"), obs.app
    if k == "press" and isinstance(a.get("ids"), list) and a["ids"] and a.get("id") is None:
        done = []
        for i in a["ids"][:30]:
            out = await run({**a, "id": i, "ids": None}, obs, hands, confirm)
            if out.stop or not out.text.startswith(("pressed", "clicked")):
                return Outcome("; ".join(done + [out.text]), stop=out.stop, raw=out.raw)
            done.append(out.text)
        return Outcome(f"pressed {len(done)} controls in order")
    e = obs.get(a.get("id")) if k in ("press", "type_into", "scroll") else None
    if k in ("press", "type_into") and e is None:
        return Outcome(f"there is no control [{a.get('id')}] in the list")

    label = name_of(e.node) if e else (a.get("path") or [""])[-1] if k == "menu" else ""
    v = gate.check(k, label=label, text=a.get("text", ""), keys=a.get("keys", ""), url=a.get("url", ""),
                   secure_field=bool(e and e.node.secure))
    if v.kind == "never":
        return Outcome(f"refused: Stream never does this ({v.why}); Isaac must do it himself", stop="never")
    if v.kind == "ask":
        if not await confirm(f"Stream wants to {v.why}: {describe(a, obs)}. Go ahead?"):
            return Outcome(f"Isaac said no to: {describe(a, obs)}", stop="denied", risky=v.why)

    try:
        if k in ("press", "type_into"):
            await _aim(hands, e)
        if k == "press":
            if "AXPress" in e.node.actions and await hands.ax_perform(app.pid, e.node.ref, "AXPress"):
                return Outcome(f"pressed [{e.id}]", risky=v.why if v.kind == "ask" else "")
            x, y, w, h = e.node.frame
            await _front(hands, app)
            await hands.pointer(x + w / 2, y + h / 2, "click", restore=True)
            return Outcome(f"clicked [{e.id}] with the mouse (it had no press action)", raw=True)
        if k == "type_into":
            text = a.get("text", "")
            raw = False
            # Search boxes and anything submitted need real key events (apps ignore a silently set value there),
            # and a set value only counts if the field really shows it afterwards.
            searchy = e.node.role == "AXSearchField" or e.node.subrole == "AXSearchField" or "search" in name_of(e.node).lower()
            # A text area (a note, a document body) gets the text added at the end; replacing it wiped a note once.
            append = e.node.role == "AXTextArea" and not a.get("replace")
            ok = (not searchy and not append and not a.get("submit") and "AXSetValue" in e.node.actions
                  and await hands.ax_set_value(app.pid, e.node.ref, text) and await _shows(hands, app, e, text))
            if not ok:
                await _front(hands, app)
                if "AXPress" in e.node.actions:
                    await hands.ax_perform(app.pid, e.node.ref, "AXPress")
                else:
                    x, y, w, h = e.node.frame
                    await hands.pointer(x + w / 2, y + h / 2, "click", restore=True)
                await asyncio.sleep(0.15)
                await hands.keys("cmd+down" if append else "cmd+a")
                await paste(hands, text)
                raw = True
            if a.get("submit"):
                await hands.keys("return")
            how = "added at the end of" if append else "typed into"
            return Outcome(f"{how} [{e.id}]" + (" and pressed return" if a.get("submit") else ""), raw=raw)
        if k == "type_keys":
            await _front(hands, app)
            await hands.type_text(str(a.get("text", "")))
            return Outcome(f'typed "{str(a.get("text", ""))[:40]}" on the keyboard', raw=True)
        if k == "menu":
            path = [str(p) for p in a.get("path") or []]
            if len(path) < 2:
                return Outcome("menu needs a path of at least two names, like [\"File\", \"New Note\"]")
            await hands.menu_press(app.pid, path)
            return Outcome("chose menu " + " > ".join(path))
        if k == "shortcut":
            await _front(hands, app)
            await hands.keys(str(a.get("keys", "")))
            return Outcome(f"pressed {a.get('keys')}", raw=True)
        if k == "open_app":
            new = await hands.open_app(str(a.get("name", "")))
            await asyncio.sleep(0.8)
            return Outcome(f"opened {new.name}", app=new)
        if k == "open_url":
            url = str(a.get("url", ""))
            if not url.startswith(("https://", "http://")):
                return Outcome("open_url needs a full https:// address")
            subprocess.run(["open", "-a", "Safari", url], check=False)
            await asyncio.sleep(1.5)
            return Outcome(f"opened {url} in Safari", app=await hands.open_app("Safari"))
        if k == "scroll":
            await _front(hands, app)
            if e is not None and "AXPress" in e.node.actions:
                pass                                            # focus stays where the brain pointed; PageDown scrolls it
            await hands.keys("pagedown" if a.get("direction", "down") == "down" else "pageup")
            return Outcome(f"scrolled {a.get('direction', 'down')}", raw=True)
        if k == "look":
            return Outcome("here is a screenshot of the window", image=await hands.capture_window(app.pid))
        if k == "list_files":
            return Outcome(list_files(str(a.get("folder", "Downloads")), str(a.get("sort") or "added"), str(a.get("kind") or "")))
        if k == "find_files":
            return Outcome(find_files(str(a.get("folder") or "~"), str(a.get("text", ""))))
        if k == "open_path":
            p = _folder(str(a.get("file", "")))
            if not p.exists():
                return Outcome(f"no such file {p}")
            subprocess.run(["open", str(p)], check=False)
            if p.is_file():
                front, changed = await _wait_front(hands, app.pid)
                return Outcome(f"opened {p.name}", app=front, data={"file": True, "front_changed": changed})
            await asyncio.sleep(0.5)
            return Outcome(f"opened {p.name}", app=await hands.front_app())
        if k == "clear_desktop":
            return await clear_desktop(hands)
        if k == "wait":
            await asyncio.sleep(1.5)
            return Outcome("waited 1.5 s")
    except Fatal as err:
        if str(err) == "NOT_FRONT":
            return Outcome(f"{k} not done: {app.name} would not come to the front, so no keys or clicks were sent")
        return Outcome(f"{k} failed: {err}")
    return Outcome(f"unknown action {k!r}")
