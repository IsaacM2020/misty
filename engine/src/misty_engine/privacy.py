"""Step C6: the apps she can't see. A list of {name, bundle} in the setting `private_apps` (None until seeded from
config/private-apps.toml, once). He changes it by talking, through the `setting` tool (name private_app). The app keeps its own
copy (ContextFrame.privateBundles) and refuses to read those apps; the engine pushes the list at connect and after each change.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from .errors import Fatal, MistyError
from .logs import log_event

LIMIT = 60


def _toml() -> Path:
    here = Path(__file__).resolve()
    for base in (here.parents[3] / "config", here.parents[2] / "config", Path.cwd() / "config"):
        if (base / "private-apps.toml").exists():
            return base / "private-apps.toml"
    return here.parents[3] / "config" / "private-apps.toml"


def seed_defaults(ctx, path: Path | None = None) -> int:
    if ctx.settings.get("private_apps") is not None:
        return 0
    try:
        rows = tomllib.loads((path or _toml()).read_text()).get("app", [])
    except (OSError, tomllib.TOMLDecodeError):
        rows = []
    apps = [{"name": str(r["name"]), "bundle": str(r["bundle"])} for r in rows if r.get("name") and r.get("bundle")]
    ctx.settings.set("private_apps", apps)
    return len(apps)


def apps(ctx) -> list[dict]:
    return list(ctx.settings.get("private_apps") or [])


def bundles(ctx) -> list[str]:
    return [a["bundle"] for a in apps(ctx)]


async def push(ctx) -> dict | None:
    """Tell the app the list. Returns what the app now holds (read back), or None when it isn't connected."""
    hands = (ctx.extras or {}).get("hands")
    if hands is None or not getattr(hands, "connected", False):
        return None
    try:
        return await hands._call("private_apps", bundles=bundles(ctx))
    except (Fatal, MistyError):
        return None


async def _running(ctx) -> list[dict]:
    hands = (ctx.extras or {}).get("hands")
    if hands is None or not getattr(hands, "connected", False):
        return []
    try:
        d = await hands._call("window_list")
    except (Fatal, MistyError):
        return []
    seen, out = set(), []
    for w in d.get("windows") or []:
        if w.get("bundle_id") and w["bundle_id"] not in seen:
            seen.add(w["bundle_id"])
            out.append({"name": str(w.get("app") or ""), "bundle": str(w["bundle_id"])})
    return out


async def change(ctx, action: str, name: str) -> dict:
    """add or remove one app by the name he said. {ok, verified, ...}. Several running matches: ok false with `which`."""
    name = (name or "").strip()
    if action not in ("add", "remove") or not name or len(name) > 80:
        return {"ok": False, "error": "say add or remove, and which app"}
    cur = apps(ctx)
    low = name.lower()
    if action == "add":
        if any(a["name"].lower() == low for a in cur):
            return {"ok": True, "verified": True, "note": "it was already private", "app": name}
        if len(cur) >= LIMIT:
            return {"ok": False, "error": f"the list is full ({LIMIT} apps)"}
        run = await _running(ctx)
        hits = [r for r in run if low in r["name"].lower()]
        exact = [r for r in hits if r["name"].lower() == low]
        hits = exact or hits
        if not hits:
            return {"ok": False, "error": f"{name} isn't open, so she can't tell which app that is: open it, then ask again"}
        if len(hits) > 1:
            return {"ok": False, "which": [h["name"] for h in hits], "error": "more than one app matches: ask him which"}
        new = hits[0]
        ctx.settings.set("private_apps", cur + [new])
        shown = new["name"]
    else:
        keep = [a for a in cur if low not in a["name"].lower()]
        if len(keep) == len(cur):
            return {"ok": False, "error": f"{name} isn't on the private list"}
        if len(cur) - len(keep) > 1 and not any(a["name"].lower() == low for a in cur):
            return {"ok": False, "which": [a["name"] for a in cur if low in a["name"].lower()],
                    "error": "more than one app matches: ask him which"}
        if any(a["name"].lower() == low for a in cur):
            keep = [a for a in cur if a["name"].lower() != low]
        ctx.settings.set("private_apps", keep)
        shown = name
    held = await push(ctx)
    now = set(bundles(ctx))
    verified = held is not None and set(held.get("bundles") or []) == now
    log_event("PRIVACY", code=f"{action}:{'ok' if verified else 'unconfirmed'}")
    return {"ok": True, "verified": verified, "app": shown, "now_private": len(now),
            **({} if verified else {"note": "saved, but the app didn't confirm it yet"})}
