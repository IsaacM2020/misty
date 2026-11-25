"""FakeHands: an in-memory Mac with a scripted accessibility tree. It records every call it gets."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import dataclasses

from misty_engine.stream.bridge import AppInfo, AxNode, MenuItem, SnapNode, WindowInfo
from misty_engine.errors import Fatal


class FakeHands:
    def __init__(self, apps: dict[str, tuple[AppInfo, list[AxNode]]] | None = None, front: str | None = None):
        self.apps = apps or {}                       # app name -> (info, tree)
        self.front = front or (next(iter(self.apps)) if self.apps else "")
        self.calls: list[tuple] = []
        self.perms = {"accessibility": True, "screen_recording": True, "post_events": True, "listen": True}
        self.frames: dict[int, list[tuple]] = {}
        self.performed: list[tuple[int, str, str]] = []
        self.values: dict[str, str] = {}
        self.animated: list[bool] = []               # whether each window move asked for the slide animation
        self.fail_actions = False                    # ax_perform and ax_set_value report False (the app is not front)
        self._input: asyncio.Queue[float] = asyncio.Queue()
        self.on_action = None                        # optional hook(kind, args) to change the tree
        self.menus: dict[int, dict[tuple, list[MenuItem]]] = {}      # pid -> {menu path: its items}; () is the top level
        self.menu_pressed: list[tuple[int, tuple]] = []
        self.windows: list[WindowInfo] = []
        self.screen: tuple = (0.0, 0.0, 1440.0, 900.0)      # the visible frame screen_frame reports
        self.find_prefix: str | None = None              # set it and ax_find hands out fresh refs (f1, f2 ...) like the real hands
        self._aliases: dict[str, str] = {}
        self.stubborn: set[tuple[int, int]] = set()      # (pid, index) windows that say "done" to a minimise but stay up
        self.restores: list[tuple] = []              # pointer clicks that asked for the pointer to be put back

    def _rec(self, *call):
        self.calls.append(call)
        if self.on_action:
            self.on_action(*call)

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]

    def _app(self, pid: int):
        return next((i, t) for i, t in self.apps.values() if i.pid == pid)

    async def permissions(self) -> dict[str, bool]:
        self._rec("permissions")
        return dict(self.perms)

    async def front_app(self) -> AppInfo:
        self._rec("front_app")
        return self.apps[self.front][0]

    async def open_app(self, name: str) -> AppInfo:
        self._rec("open_app", name)
        if name not in self.apps:
            from misty_engine.errors import Fatal
            raise Fatal("APP_NOT_FOUND")
        self.front = name
        return self.apps[name][0]

    async def ax_tree(self, pid: int, max_nodes: int) -> list[AxNode]:
        self._rec("ax_tree", pid)
        return list(self._app(pid)[1])[:max_nodes]

    @staticmethod
    def _snap(n: AxNode) -> SnapNode:
        if isinstance(n, SnapNode):
            return n
        return SnapNode(**{f.name: getattr(n, f.name) for f in dataclasses.fields(AxNode)})

    async def ax_snapshot(self, pid: int, max_nodes: int, list_preview: int) -> list[SnapNode]:
        self._rec("ax_snapshot", pid)
        nodes = [self._snap(n) for n in self._app(pid)[1]]
        first = [n for n in nodes if n.region != "content" or n.focused]       # the real walk puts these first
        return (first + [n for n in nodes if n not in first])[:max_nodes]

    async def ax_find(self, pid: int, query: str, limit: int) -> list[SnapNode]:
        self._rec("ax_find", pid, query)
        q = query.lower()
        hits = [self._snap(n) for n in self._app(pid)[1]
                if any(q in str(getattr(n, f, "")).lower() for f in ("label", "value", "desc", "help", "ident"))]
        if self.find_prefix:
            fresh = []
            for k, n in enumerate(hits[:limit], 1):
                alias = f"{self.find_prefix}{k}"
                self._aliases[alias] = n.ref
                fresh.append(dataclasses.replace(n, ref=alias))
            return fresh
        return hits[:limit]

    async def menu_list(self, pid: int, path: list[str]) -> list[MenuItem]:
        self._rec("menu_list", pid, tuple(path))
        items = self.menus.get(pid, {}).get(tuple(path))
        if items is None:
            raise Fatal("NO_SUCH_MENU")
        return list(items)

    async def menu_press(self, pid: int, path: list[str]) -> bool:
        self._rec("menu_press", pid, tuple(path))
        parent = self.menus.get(pid, {}).get(tuple(path[:-1]), [])
        if not any(i.title == path[-1] and i.enabled for i in parent):
            raise Fatal("NO_SUCH_MENU")
        self.menu_pressed.append((pid, tuple(path)))
        return True

    async def window_list(self) -> list[WindowInfo]:
        self._rec("window_list")
        return list(self.windows)

    async def set_minimised(self, pid: int, index: int, minimised: bool) -> bool:
        self._rec("set_minimised", pid, index, minimised)
        for i, w in enumerate(self.windows):
            if w.pid == pid and w.index == index:
                if (pid, index) not in self.stubborn:
                    self.windows[i] = dataclasses.replace(w, minimised=minimised)
                return True
        return False

    async def ax_perform(self, pid: int, ref: str, action: str) -> bool:
        self._rec("ax_perform", pid, ref, action)
        if self.fail_actions:
            return False
        ref = self._aliases.get(ref, ref)
        node = next(n for n in self._app(pid)[1] if n.ref == ref)
        if action not in node.actions:
            return False
        self.performed.append((pid, ref, action))
        return True

    async def ax_set_value(self, pid: int, ref: str, text: str) -> bool:
        self._rec("ax_set_value", pid, ref, text)
        if self.fail_actions:
            return False
        ref = self._aliases.get(ref, ref)
        node = next(n for n in self._app(pid)[1] if n.ref == ref)
        if "AXSetValue" not in node.actions:
            return False
        self.values[ref] = text
        tree = self._app(pid)[1]
        for i, n in enumerate(tree):                 # the field now shows it, like a real one
            if n.ref == ref:
                tree[i] = dataclasses.replace(n, value=text)
        return True

    async def capture_window(self, pid: int) -> bytes:
        self._rec("capture_window", pid)
        return b"\x89PNG-fake"

    async def pointer(self, x: float, y: float, kind: str, restore: bool = False) -> None:
        self._rec("pointer", x, y, kind)
        if restore:
            self.restores.append((x, y, kind))

    async def keys(self, combo: str) -> None:
        self._rec("keys", combo)

    async def type_text(self, text: str) -> None:
        self._rec("type_text", text)

    async def window_frames(self, pid: int) -> list[tuple]:
        self._rec("window_frames", pid)
        return list(self.frames.get(pid, [(0.0, 0.0, 800.0, 600.0)]))

    async def set_window_frame(self, pid: int, index: int, frame: tuple, animate: bool = False) -> bool:
        self._rec("set_window_frame", pid, index, frame)
        self.animated.append(animate)
        fs = self.frames.setdefault(pid, [(0.0, 0.0, 800.0, 600.0)])
        fs[index] = frame
        for i, w in enumerate(self.windows):
            if w.pid == pid and w.index == index:
                self.windows[i] = dataclasses.replace(w, frame=tuple(frame))
        return True

    async def screen_frame(self) -> tuple:
        self._rec("screen_frame")
        return self.screen

    async def cursor(self, do: str, frame=None, label: str = "") -> float:
        self._rec("cursor", do)
        return 0.0

    def emit_user_input(self, ts: float | None = None) -> None:
        import time
        self._input.put_nowait(time.time() if ts is None else ts)

    async def user_input(self) -> AsyncIterator[float]:
        while True:
            yield await self._input.get()


def node(ref, role, label="", value="", frame=(10.0, 10.0, 80.0, 24.0), secure=False, actions=("AXPress",), depth=1,
         **extra):
    """An AxNode; with any of desc, help, ident, subrole, selected, region, rows, path it is a SnapNode."""
    if extra:
        return SnapNode(ref, role, label, value, tuple(frame), secure, list(actions), depth, **extra)
    return AxNode(ref, role, label, value, tuple(frame), secure, list(actions), depth)


def textedit() -> tuple[AppInfo, list[AxNode]]:
    info = AppInfo("com.apple.TextEdit", 501, "TextEdit")
    return info, [node("a1", "AXWindow", "Untitled", actions=(), depth=0),
                  node("a2", "AXTextArea", "", actions=("AXSetValue",), frame=(0, 40, 400, 300)),
                  node("a3", "AXButton", "Save"), node("a4", "AXButton", "Close")]


class ScreenHands:
    """Just enough hands for read_screen: scripted answers for read_window_text and ocr_window."""
    connected = True

    def __init__(self, ax=None, ocr=None, fail=None):
        self.ax, self.ocr, self.fail, self.calls = ax or {}, ocr or {}, fail or {}, []

    async def _call(self, op, **args):
        from misty_engine.errors import Fatal
        self.calls.append(op)
        if op == "last_app":
            return {"pid": 1, "name": "App", "bundle_id": "x"}
        if op in self.fail:
            raise Fatal(self.fail[op])
        return dict({"read_window_text": self.ax, "ocr_window": self.ocr}[op])

    async def capture_window(self, pid):
        from misty_engine.errors import Fatal
        self.calls.append("capture_window")
        raise Fatal("PRIVATE_APP" if self.ax.get("private") else "CAPTURE_FAILED")
