"""Pack small items together, keep large ones alone. Pure code."""
from __future__ import annotations

from typing import Sequence


def make_batches(items: Sequence[tuple[str, int]], *, max_items: int = 8, max_tokens: int = 6000,
                 small: int = 1200) -> list[list[str]]:
    out: list[list[str]] = []
    cur: list[str] = []
    used = 0
    for item_id, tokens in items:
        if tokens > small:
            out.append([item_id])
            continue
        if cur and (len(cur) >= max_items or used + tokens > max_tokens):
            out.append(cur)
            cur, used = [], 0
        cur.append(item_id)
        used += tokens
    if cur:
        out.append(cur)
    return out
