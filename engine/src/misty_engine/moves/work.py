"""D2: the safe moves a "let's work" protocol needs. Timers, her allow-listed shortcuts, Focus, window layouts, hiding apps,
a priority line. Nothing here writes to his accounts or his files; every move proves itself by reading back."""
from __future__ import annotations

import asyncio
import re

from .. import timers
from ..errors import Fatal, MistyError
from .base import Move, Outcome, Param
from .registry import register


# ---- timers

async def _timer_start(ctx, p) -> Outcome:
    tid = timers.start(ctx, p["minutes"], p.get("label", ""))
    what = f"started a {p['minutes']} minute timer" + (f" for {p['label']}" if p.get("label") else "")
    return Outcome(True, what=what, undo={"move": "timer_stop", "params": {"label": p.get("label", "")}}, data={"timer_id": tid})


async def _timer_started(ctx, p, out) -> bool:
    r = timers.get(ctx, out.data["timer_id"])
    return r is not None and r["status"] == "active" and r["ends_at"] > ctx.clock.now()


async def _timer_stop(ctx, p) -> Outcome:
    ids = timers.stop(ctx, p.get("label", ""))
    if not ids:                                       # D10: the goal (no such timer running) already holds; a protocol must not die on it
        return Outcome(True, what="no timer was running" + (f" for {p['label']}" if p.get("label") else "") + ", so there was nothing to stop",
                       data={"timer_ids": [], "label": p.get("label", "")})
    return Outcome(True, what=f"stopped {len(ids)} timer" + ("s" if len(ids) > 1 else ""), data={"timer_ids": ids})


async def _timer_stopped(ctx, p, out) -> bool:
    label = (p.get("label") or "").strip().lower()
    still = [r for r in timers.active(ctx) if not label or label in r["label"].lower()]
    return not still and all(timers.get(ctx, i)["status"] == "stopped" for i in out.data["timer_ids"])


# ---- shortcuts (his own, by name; the allow list is built by talking)

async def _exec(*argv: str, timeout: float = 60.0) -> tuple[int, str]:
    """Runs a command, returns (exit code, output). Tests replace this: no test ever runs a real shortcut."""
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timed out"
    return proc.returncode or 0, out.decode(errors="replace")[:500]


async def _shortcut_names() -> list[str]:
    code, out = await _exec("shortcuts", "list", timeout=15)
    return [l.strip() for l in out.splitlines() if l.strip()] if code == 0 else []


def _same(a: str, b: str) -> str | None:
    return a if a.lower() == b.lower() else None


async def _shortcut_run(ctx, p) -> Outcome:
    allow = [str(x) for x in (ctx.settings.get("shortcut_allow") or [])]
    name = next((a for a in allow if a.lower() == p["name"].lower()), None)
    if name is None:
        return Outcome(False, what=f"{p['name']} isn't on the list of shortcuts she may run: he can say \"you can run my {p['name']} shortcut\"")
    code, out = await _exec("shortcuts", "run", name)
    return Outcome(code == 0, what=f"ran the {name} shortcut" if code == 0 else f"the {name} shortcut failed (exit {code})", data={"exit": code})


async def _exit_zero(ctx, p, out) -> bool:
    return out.data.get("exit") == 0


async def _shortcut_allow(ctx, p) -> Outcome:
    names = await _shortcut_names()
    real = next((n for n in names if n.lower() == p["name"].lower()), None)
    if real is None:
        return Outcome(False, what=f"there is no shortcut called {p['name']} in his Shortcuts")
    cur = [str(x) for x in (ctx.settings.get("shortcut_allow") or [])]
    cur = [c for c in cur if c.lower() != real.lower()]
    if p["allow"]:
        cur.append(real)
    with ctx.con:
        ctx.settings.set("shortcut_allow", cur)
    return Outcome(True, what=f"{'may now run' if p['allow'] else 'may no longer run'} his {real} shortcut", data={"name": real})


async def _allow_read_back(ctx, p, out) -> bool:
    has = any(str(x).lower() == out.data["name"].lower() for x in ctx.settings.get("shortcut_allow") or [])
    return has == bool(p["allow"])


# ---- Focus: two of his own shortcuts

FOCUS_NAMES = {"on": ("Focus On", "Misty Focus On"), "off": ("Focus Off", "Misty Focus Off")}
FOCUS_HOWTO = ("he has no Focus shortcuts yet. Make two in the Shortcuts app: one called Focus On (action: Set Focus, Do Not Disturb, "
               "turn on) and one called Focus Off (the same action, turn off). Then she can do it")


async def _focus(ctx, p) -> Outcome:
    names = await _shortcut_names()
    name = next((n for want in FOCUS_NAMES[p["state"]] for n in names if n.lower() == want.lower()), None)
    if name is None:
        return Outcome(False, what=FOCUS_HOWTO)
    code, out = await _exec("shortcuts", "run", name)
    return Outcome(code == 0, what=f"turned Focus {p['state']}" if code == 0 else f"the {name} shortcut failed (exit {code})", data={"exit": code})


# ---- windows

def _hands(ctx):
    h = (ctx.extras or {}).get("hands")
    return h if h is not None and getattr(h, "connected", False) else None


LAYOUT_N = {"full": 1, "left_right": 2, "thirds": 3}


def targets(layout: str, screen: list[float], n: int) -> list[list[float]]:
    x, y, w, h = screen
    if layout == "full":
        return [[x, y, w, h]]
    k = 2 if layout == "left_right" else 3
    return [[x + i * w / k, y, w / k, h] for i in range(k)][:n]


async def _windows_arrange(ctx, p) -> Outcome:
    hands = _hands(ctx)
    if hands is None:
        return Outcome(False, what="Misty's hands aren't connected, so she can't move windows right now")
    apps = [a.strip() for a in re.split(r"[,;]", p["apps"]) if a.strip()]
    if len(apps) != LAYOUT_N[p["layout"]]:
        return Outcome(False, what=f"{p['layout']} takes {LAYOUT_N[p['layout']]} app{'s' if LAYOUT_N[p['layout']] > 1 else ''}, not {len(apps)}")
    try:
        wins = (await hands._call("window_list")).get("windows") or []
        screen = (await hands._call("screen_frame")).get("frame") or [0, 0, 1440, 900]
        spots = targets(p["layout"], [float(v) for v in screen], len(apps))
        done = []
        for app, spot in zip(apps, spots):
            w = next((w for w in wins if app.lower() in str(w.get("app", "")).lower() and not w.get("minimised") and not w.get("fullscreen")), None)
            if w is None:
                return Outcome(False, what=f"{app} has no window she can move (not open, minimised or full screen)")
            await hands._call("set_window_frame", pid=w["pid"], index=w["index"], frame=spot, animate=False)
            done.append({"pid": w["pid"], "index": w["index"], "frame": spot})
    except (Fatal, MistyError) as e:
        return Outcome(False, what=f"couldn't move the windows ({e})")
    return Outcome(True, what=f"put {' and '.join(apps)} {p['layout'].replace('_', ' ')}", data={"moved": done})


async def _frames_match(ctx, p, out) -> bool:
    hands = _hands(ctx)
    for m in out.data["moved"]:
        got = (await hands._call("window_frames", pid=m["pid"])).get("frames") or []
        if len(got) <= m["index"] or any(abs(float(a) - float(b)) > 12 for a, b in zip(got[m["index"]], m["frame"])):
            return False
    return True


# ---- hide other apps (hide, never quit: quitting can lose work)

async def _apps_hide(ctx, p) -> Outcome:
    hands = _hands(ctx)
    if hands is None:
        return Outcome(False, what="Misty's hands aren't connected, so she can't hide apps right now")
    keep = [a.strip() for a in re.split(r"[,;]", p.get("except", "")) if a.strip()]
    try:
        d = await hands._call("hide_apps", **{"except": keep})
    except (Fatal, MistyError) as e:
        return Outcome(False, what=f"couldn't hide the apps ({e})")
    hidden = d.get("hidden") or []
    return Outcome(True, what=f"hid {len(hidden)} app{'s' if len(hidden) != 1 else ''}" + (f", kept {', '.join(keep)}" if keep else ""),
                   data={"visible": d.get("visible") or [], "keep": keep})


async def _only_kept_visible(ctx, p, out) -> bool:
    keep = {k.lower() for k in out.data["keep"]}
    return all(str(v).lower() in keep for v in out.data["visible"])


# ---- a line on Priorities (a local file of hers, so safe)

_DUE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def _priority_add(ctx, p) -> Outcome:
    if ctx.wiki is None:
        return Outcome(False, what="her wiki isn't open")
    if p.get("due") and not _DUE.match(p["due"]):
        return Outcome(False, what="due must be a date like 2026-11-20")
    from ..wiki import priorities
    pid = priorities.add_line(ctx.wiki, ctx.clock, p["text"], p.get("due"))
    return Outcome(True, what=f"added \"{p['text']}\" to Priorities", data={"priority_id": pid})


async def _priority_there(ctx, p, out) -> bool:
    from ..wiki import priorities
    return any(l["id"] == out.data["priority_id"] for l in priorities.get(ctx.wiki)["lines"])


register(Move("timer_start", "start a timer; when it ends she tells him", {"minutes": Param(int, lo=1, hi=480), "label": Param(str, required=False, max_len=60)},
              "safe", _timer_start, _timer_started,
              says=("start a 25 minute timer", "set a timer for 45 minutes for maths", "give me ten minutes on the clock"),
              not_mine=(("tell me when the match starts", "follow"), ("what's on my calendar this afternoon", "calendar"))))
register(Move("timer_stop", "stop a timer (all of them if no label)", {"label": Param(str, required=False, max_len=60)}, "safe", _timer_stop, _timer_stopped,
              says=("stop the timer", "cancel my maths timer", "kill the timer"),
              not_mine=(("stop following the match", "follow"), ("stop talking", "setting"))))
register(Move("shortcut_run", "run one of his Shortcuts he allowed by name", {"name": Param(str, max_len=80)}, "safe", _shortcut_run, _exit_zero, no_text=True,
              says=("run my Math Class shortcut", "run the English Class shortcut", "do my Spanish Class shortcut"),
              not_mine=(("click the Run button in the Shortcuts app", "do"), ("what shortcuts do I have", "recall"))))
register(Move("shortcut_allow", "allow or stop allowing one of his Shortcuts to be run by name", {"name": Param(str, max_len=80), "allow": Param(bool)}, "ask",
              _shortcut_allow, _allow_read_back, preview=lambda p: (f"Let Misty run your \"{p['name']}\" shortcut by name?" if p["allow"]
                                                                     else f"Stop letting Misty run your \"{p['name']}\" shortcut?"),
              says=("you can run my Math Class shortcut", "allow the Spanish Class shortcut", "you're not allowed to run my Immich shortcut anymore"),
              not_mine=(("remember my shortcut is called Math Class", "remember"), ("add a new shortcut that opens Notes", "do"))))
register(Move("focus", "turn Focus (Do Not Disturb) on or off through his two Focus shortcuts", {"state": Param(str, choices=("on", "off"))}, "safe", _focus, _exit_zero,
              says=("turn on do not disturb", "put focus on", "I'm done, turn focus off"),
              not_mine=(("be quiet, stop speaking your replies", "setting"), ("don't tell me anything until tomorrow", "speak_up"))))
register(Move("windows_arrange", "put app windows side by side: layout left_right (2 apps), full (1), thirds (3); apps is a comma list of names",
              {"layout": Param(str, choices=tuple(LAYOUT_N)), "apps": Param(str, max_len=200)}, "safe", _windows_arrange, _frames_match,
              says=("put Safari and Notes side by side", "split my screen with Chrome on the left and Obsidian on the right", "make Preview full screen size"),
              not_mine=(("what window am I looking at", "look"), ("click the close button on Safari", "do"))))
register(Move("apps_hide", "hide every app except the ones named (comma list, may be empty); never quits anything", {"except": Param(str, required=False, max_len=200)},
              "safe", _apps_hide, _only_kept_visible,
              says=("hide everything except Notes and Chrome", "get everything else out of the way", "hide all my other apps"),
              not_mine=(("close Slack", "do"), ("what apps do I have open", "look"))))
register(Move("priority_add", "add a line to his Priorities list (due is a date, optional)", {"text": Param(str, max_len=160), "due": Param(str, required=False, max_len=10)},
              "safe", _priority_add, _priority_there,
              says=("add finish the chem lab report to my priorities", "put revise organic chemistry on my priority list", "priority: email Mr Tan, due 2026-11-20"),
              not_mine=(("remember that my chem test is on Friday", "remember"), ("what should I work on now", "work_on"))))
