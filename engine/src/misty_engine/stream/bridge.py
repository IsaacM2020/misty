"""Stream bridge: how the engine reaches the Mac's other apps (ported from hands-v2 act/hands.py).

Accessibility is not inherited by the engine child process, so the Swift app does the looking and the pressing. The
engine sends `hands.request {req_id, op, args}` on its socket; the app answers with the RPC `hands.result`. The same
app also reports keys and mouse moves the person makes (`hands.user_input`), so a run can notice a take-over.
"""
from __future__ import annotations

import asyncio
import base64
import secrets
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Protocol

from ..errors import Fatal, Retryable

Frame = tuple[float, float, float, float]


@dataclass(frozen=True)
class AppInfo:
    bundle_id: str
    pid: int
    name: str


@dataclass(frozen=True)
class AxNode:
    ref: str
    role: str
    label: str
    value: str
    frame: Frame
    secure: bool
    actions: list[str]
    depth: int
    focused: bool = False


@dataclass(frozen=True)
class SnapNode(AxNode):
    """One control of the screen map (hands v2 spec 5.1): an AxNode plus why it was kept and where it sits."""
    subrole: str = ""
    desc: str = ""
    help: str = ""
    ident: str = ""
    selected: bool = False
    region: str = "content"              # toolbar | sheet | focus | titlebar | content | menu
    rows: int = -1                       # the full child count of a collapsed list, else -1
    path: tuple[str, ...] = ()           # names of the ancestors, outermost first


@dataclass(frozen=True)
class MenuItem:
    title: str
    shortcut: str
    enabled: bool
    marked: bool = False          # ticked: the current sort order, a switched-on view option


@dataclass(frozen=True)
class WindowInfo:
    pid: int
    bundle_id: str
    app: str
    index: int
    title: str
    frame: Frame
    minimised: bool
    fullscreen: bool


class Hands(Protocol):
    async def permissions(self) -> dict[str, bool]: ...
    async def front_app(self) -> AppInfo: ...
    async def open_app(self, name: str) -> AppInfo: ...
    async def ax_tree(self, pid: int, max_nodes: int) -> list[AxNode]: ...
    async def ax_snapshot(self, pid: int, max_nodes: int, list_preview: int) -> list[SnapNode]: ...
    async def ax_find(self, pid: int, query: str, limit: int) -> list[SnapNode]: ...
    async def menu_list(self, pid: int, path: list[str]) -> list[MenuItem]: ...
    async def menu_press(self, pid: int, path: list[str]) -> bool: ...
    async def window_list(self) -> list[WindowInfo]: ...
    async def set_minimised(self, pid: int, index: int, minimised: bool) -> bool: ...
    async def ax_perform(self, pid: int, ref: str, action: str) -> bool: ...
    async def ax_set_value(self, pid: int, ref: str, text: str) -> bool: ...
    async def capture_window(self, pid: int) -> bytes: ...
    async def pointer(self, x: float, y: float, kind: str, restore: bool = False) -> None: ...
    async def keys(self, combo: str) -> None: ...
    async def type_text(self, text: str) -> None: ...
    async def window_frames(self, pid: int) -> list[Frame]: ...
    async def set_window_frame(self, pid: int, index: int, frame: Frame, animate: bool = False) -> bool: ...
    async def screen_frame(self) -> Frame: ...
    async def cursor(self, do: str, frame: Frame | None = None, label: str = "") -> float: ...
    def user_input(self) -> AsyncIterator[float]: ...


class AppHands:
    """The Swift app is the hands. `notify` is the engine's socket broadcast."""

    def __init__(self, notify: Callable[[str, dict], Awaitable[None]], *, timeout: float = 10.0):
        self._notify, self._timeout = notify, timeout
        self._waiting: dict[str, asyncio.Future] = {}
        self._input: asyncio.Queue[float] = asyncio.Queue(maxsize=256)
        self.connected = False

    # ---- the app side of the wire
    def register(self) -> None:
        self.connected = True

    def result(self, req_id: str, ok: bool, data, code: str | None) -> None:
        fut = self._waiting.get(req_id)
        if fut is not None and not fut.done():
            fut.set_result((ok, data, code))

    def user_input_seen(self, ts: float) -> None:
        if self._input.full():
            self._input.get_nowait()
        self._input.put_nowait(float(ts))

    # ---- the engine side
    SLOW_OPS = {"capture_window": 25.0, "open_app": 20.0, "ocr_window": 25.0, "class_start": 20.0}

    async def _call(self, op: str, **args):
        if not self.connected:
            raise Fatal("HANDS_UNAVAILABLE")
        req_id = "rq_" + secrets.token_hex(6)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[req_id] = fut
        try:
            await self._notify("hands.request", {"req_id": req_id, "op": op, "args": args})
            try:
                ok, data, code = await asyncio.wait_for(fut, self.SLOW_OPS.get(op, self._timeout))
            except asyncio.TimeoutError:
                raise Retryable("HANDS_TIMEOUT") from None
        finally:
            self._waiting.pop(req_id, None)
        if not ok:
            raise Fatal(code or "HANDS_FAILED")
        return data or {}

    async def permissions(self) -> dict[str, bool]:
        d = await self._call("permissions")
        return {k: bool(d.get(k)) for k in ("accessibility", "screen_recording", "post_events", "listen")}

    async def front_app(self) -> AppInfo:
        return _app(await self._call("front_app"))

    async def open_app(self, name: str) -> AppInfo:
        return _app(await self._call("open_app", name=name))

    async def ax_tree(self, pid: int, max_nodes: int) -> list[AxNode]:
        d = await self._call("ax_tree", pid=pid, max_nodes=max_nodes)
        return [AxNode(str(n["ref"]), str(n["role"]), str(n.get("label", "")), str(n.get("value", "")),
                       tuple(float(x) for x in n["frame"]), bool(n.get("secure")), list(n.get("actions", [])),
                       int(n.get("depth", 0)), bool(n.get("focused"))) for n in d.get("nodes", [])]

    async def ax_snapshot(self, pid: int, max_nodes: int, list_preview: int) -> list[SnapNode]:
        d = await self._call("ax_snapshot", pid=pid, max_nodes=max_nodes, list_preview=list_preview)
        return [_snap(n) for n in d.get("nodes", [])]

    async def ax_find(self, pid: int, query: str, limit: int) -> list[SnapNode]:
        d = await self._call("ax_find", pid=pid, query=query, limit=limit)
        return [_snap(n) for n in d.get("nodes", [])]

    async def menu_list(self, pid: int, path: list[str]) -> list[MenuItem]:
        d = await self._call("menu_list", pid=pid, path=list(path))
        return [MenuItem(str(i.get("title", "")), str(i.get("shortcut", "")), bool(i.get("enabled", True)), bool(i.get("marked", False)))
                for i in d.get("items", [])]

    async def menu_press(self, pid: int, path: list[str]) -> bool:
        return bool((await self._call("menu_press", pid=pid, path=list(path))).get("done"))

    async def window_list(self) -> list[WindowInfo]:
        d = await self._call("window_list")
        return [WindowInfo(int(w["pid"]), str(w.get("bundle_id", "")), str(w.get("app", "")), int(w.get("index", 0)),
                           str(w.get("title", "")), tuple(float(x) for x in w.get("frame", [0, 0, 0, 0])),
                           bool(w.get("minimised")), bool(w.get("fullscreen"))) for w in d.get("windows", [])]

    async def set_minimised(self, pid: int, index: int, minimised: bool) -> bool:
        return bool((await self._call("set_minimised", pid=pid, index=index, minimised=bool(minimised))).get("done"))

    async def probe_post_to_pid(self, pid: int, x: float, y: float) -> bool:
        """Debug only: a click posted to one process; True if the visible pointer moved."""
        return bool((await self._call("probe_post_to_pid", pid=pid, x=x, y=y)).get("pointer_moved"))

    async def ax_perform(self, pid: int, ref: str, action: str) -> bool:
        return bool((await self._call("ax_perform", pid=pid, ref=ref, action=action)).get("done"))

    async def ax_set_value(self, pid: int, ref: str, text: str) -> bool:
        return bool((await self._call("ax_set_value", pid=pid, ref=ref, text=text)).get("done"))

    async def capture_window(self, pid: int) -> bytes:
        return base64.b64decode((await self._call("capture_window", pid=pid)).get("png", ""))

    async def pointer(self, x: float, y: float, kind: str, restore: bool = False) -> None:
        await self._call("pointer", x=x, y=y, kind=kind, **({"restore": True} if restore else {}))

    async def keys(self, combo: str) -> None:
        await self._call("keys", combo=combo)

    async def type_text(self, text: str) -> None:
        await self._call("type_text", text=text)

    async def window_frames(self, pid: int) -> list[Frame]:
        d = await self._call("window_frames", pid=pid)
        return [tuple(float(x) for x in f) for f in d.get("frames", [])]

    async def set_window_frame(self, pid: int, index: int, frame: Frame, animate: bool = False) -> bool:
        return bool((await self._call("set_window_frame", pid=pid, index=index, frame=list(frame), animate=animate)).get("done"))

    async def screen_frame(self) -> Frame:
        return tuple(float(x) for x in (await self._call("screen_frame")).get("frame", [0, 0, 0, 0]))

    async def cursor(self, do: str, frame: Frame | None = None, label: str = "") -> float:
        """Stream's own on-screen cursor (a picture only): aim / click / think / hide. Returns the glide's seconds."""
        d = await self._call("cursor", do=do, label=label, **({"frame": list(frame)} if frame else {}))
        return float(d.get("secs") or 0)

    async def user_input(self) -> AsyncIterator[float]:
        while True:
            yield await self._input.get()


def _app(d: dict) -> AppInfo:
    return AppInfo(str(d.get("bundle_id", "")), int(d.get("pid", 0)), str(d.get("name", "")))


def _snap(n: dict) -> SnapNode:
    return SnapNode(str(n["ref"]), str(n.get("role", "")), str(n.get("label", "")), str(n.get("value", "")),
                    tuple(float(x) for x in n.get("frame", [0, 0, 0, 0])), bool(n.get("secure")),
                    list(n.get("actions", [])), int(n.get("depth", 0)), bool(n.get("focused")),
                    subrole=str(n.get("subrole", "")), desc=str(n.get("desc", "")), help=str(n.get("help", "")),
                    ident=str(n.get("ident", "")), selected=bool(n.get("selected")), region=str(n.get("region", "content")),
                    rows=int(n.get("rows", -1)), path=tuple(str(p) for p in n.get("path", [])))
