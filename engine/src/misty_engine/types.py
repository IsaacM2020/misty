"""Small shared types and id helpers."""
from __future__ import annotations

import hashlib
import math
import secrets
from dataclasses import dataclass, field
from typing import Any, Literal

Phase = Literal["build", "live"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def item_id_for(source: str, locator: str) -> str:
    h = hashlib.sha256(f"{source}\0{locator}".encode()).hexdigest()
    return "it_" + h[:16]


def fingerprint(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def approx_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


@dataclass(frozen=True)
class RawItem:
    source: str
    locator: str
    kind: str
    title: str
    created_at: str | None = None
    modified_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return item_id_for(self.source, self.locator)


@dataclass
class Job:
    id: str
    kind: str
    item_id: str | None
    payload: dict[str, Any]
    cloud: bool
    status: str
    attempts: int
    next_run_at: float
    error_code: str | None
