"""The Source protocol every reader implements."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterator, Protocol

from ..types import RawItem


@dataclass(frozen=True)
class ChatInfo:
    chat_id: str
    name: str
    last_message_at: str | None
    count: int


class Source(Protocol):
    name: str
    stats: Counter

    def discover(self, cursor: str | None, limit: int | None = None) -> Iterator[RawItem]: ...

    def load_text(self, item: RawItem) -> str: ...

    def next_cursor(self) -> str | None: ...


def scope_cursor(scope: set[str], n: int) -> str:
    """'<hash of the chat selection>:<last id>'. Picking or un-excluding a chat changes the hash,
    so its history is read from the start instead of being skipped by an old cursor."""
    import hashlib
    h = hashlib.sha256("\0".join(sorted(scope)).encode()).hexdigest()[:8]
    return f"{h}:{n}"


def parse_scope_cursor(cursor: str | None, scope: set[str]) -> int:
    if not cursor or ":" not in cursor:
        return 0
    h, n = cursor.split(":", 1)
    return int(n) if scope_cursor(scope, 0).split(":")[0] == h else 0
