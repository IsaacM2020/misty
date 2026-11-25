"""Online/offline state, driven by the app's NWPathMonitor and by failed calls."""
from __future__ import annotations

from typing import Callable


class NetState:
    def __init__(self) -> None:
        self.online = True
        self._listeners: list[Callable[[bool, str], None]] = []

    def on_change(self, cb: Callable[[bool, str], None]) -> None:
        self._listeners.append(cb)

    def set_online(self, online: bool, source: str) -> bool:
        changed = online != self.online
        self.online = online
        if changed:
            for cb in self._listeners:
                cb(online, source)
        return changed
