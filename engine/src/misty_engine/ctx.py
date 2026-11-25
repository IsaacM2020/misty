"""Shared engine context passed to handlers and api methods."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .clock import Clock
from .config import Pinned, Settings
from .jobs import JobQueue
from .net import NetState
from .openrouter import OpenRouterClient
from .paths import MistyPaths
from .spend import SpendMeter


@dataclass
class Ctx:
    paths: MistyPaths
    pinned: Pinned
    con: sqlite3.Connection
    settings: Settings
    queue: JobQueue
    spend: SpendMeter
    net: NetState
    clock: Clock
    openrouter: OpenRouterClient
    embedder: Any = None                                   # embed.Embedder
    index: Any = None                                      # index.Index
    jev: Any = None                                        # jev.JevClient
    wiki: Any = None                                       # wiki.store.WikiStore
    runner: Any = None                                     # runner.ApiRunner, the one model runner
    sources: dict[str, Any] = field(default_factory=dict)  # name -> Source
    notify: Callable[[str, dict], Awaitable[None]] | None = None
    open_doc: str = ""                                      # E2: the path or link he has open this turn ("this"); empty when private
    open_near: str = ""                                    # E9: the words near what he points at in it (his highlight, else the text near)
    slate_page: Any = None                                 # S8: the slate on screen (its read-back or a "wait" finds it here)
    slate_at: float = 0.0
    slate_pending: Any = None                              # when a read-back was asked; None once answered or forgotten
    slate_saved: Any = None
    slate_lesson: Any = None                               # S10: which lesson is on the Slate and how many steps of it
    slate_id: int = 0                                      # S9: the saved row of the slate on screen
    turn: str = ""                                         # S9: the chat turn being answered (set by chat.send)
    extras: dict[str, Any] | None = None   # later milestones attach embedder, index, sources, wiki, ...

    def paid_allowed(self) -> bool:
        return self.settings.get("paid_calls_allowed") is True

    def card(self) -> str:
        """The about-isaac card sent with every Jev call: the wiki's card once it exists, else the bootstrap one."""
        from .config import default_pinned_path
        for p in (self.paths.wiki / "about-isaac.md", default_pinned_path().parent / "bootstrap-about-isaac.md"):
            try:
                text = p.read_text()
            except OSError:
                continue
            if p.name == "about-isaac.md" and text.startswith("---"):
                text = text.split("---", 2)[-1]
            text = text.strip()
            if text and text != "(empty)":
                return text
        return ""
