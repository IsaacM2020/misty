"""App Cards: what Stream knows about an app before looking at its window.

Built automatically from the app's own menu bar (every menu item and its shortcut), plus hand-written tips in
stream/cards/<bundle id>.toml for apps whose menus don't tell the whole story (Clock's timer, Alfred).
Cached per app for a week in $MISTY_HOME/stream/appcards/.
"""
from __future__ import annotations

import json
import time
import tomllib
from pathlib import Path

from .bridge import AppInfo

CARDS_DIR = Path(__file__).parent / "cards"
MAX_CHARS = 3000
TTL_S = 7 * 86400
_SKIP_MENUS = {"Apple", "Help"}


def _short(s: str) -> str:
    return s.replace("Meta+", "cmd+").replace("Control+", "ctrl+").replace("Alt+", "alt+").replace("Shift+", "shift+").lower()


def tips(bundle_id: str) -> str:
    p = CARDS_DIR / f"{bundle_id}.toml"
    if not p.exists():
        return ""
    d = tomllib.loads(p.read_text())
    return "\n".join(f"- {t}" for t in d.get("tips", []))


async def menus(hands, app: AppInfo) -> str:
    """'File: New Note (cmd+n), New Folder (cmd+shift+n) | Edit: ...' with disabled items left out."""
    top = [m.title for m in await hands.menu_list(app.pid, []) if m.title and m.title not in _SKIP_MENUS]
    out: list[str] = []
    for title in top[1:] if top and top[0] == app.name else top:          # the first menu is the app's own name menu
        try:
            items = await hands.menu_list(app.pid, [title])
        except Exception:
            continue
        names = [i.title + (f" ({_short(i.shortcut)})" if i.shortcut else "") for i in items if i.title and i.enabled]
        if names:
            out.append(f"{title}: " + ", ".join(names))
    return " | ".join(out)


async def card(hands, app: AppInfo, home: Path) -> str:
    cache = home / "stream" / "appcards" / f"{app.bundle_id or app.name}.json"
    text = None
    if cache.exists():
        d = json.loads(cache.read_text())
        if time.time() - d.get("at", 0) < TTL_S:
            text = d["menus"]
    if text is None:
        try:
            text = await menus(hands, app)
        except Exception:
            text = ""
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"at": time.time(), "menus": text}))
    t = tips(app.bundle_id)
    out = f"Menus of {app.name} (use menu with the path, e.g. [\"File\", \"New Note\"]): {text[:MAX_CHARS]}" if text else ""
    if t:
        out += f"\nTips for {app.name}:\n{t}"
    return out
