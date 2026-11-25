"""Decoders for Apple's message formats."""
from __future__ import annotations

from datetime import datetime, timezone

APPLE_EPOCH = 978307200      # 2001-01-01 in unix seconds


def decode_attributed_body(blob: bytes | None) -> str | None:
    """Pull the plain text out of a Messages `attributedBody` typedstream blob.

    Newer macOS stores the text only here (message.text is NULL). The string follows the class name
    `NSString` and 5 marker bytes, with a 1, 3 (0x81) or 5 (0x82) byte little-endian length.
    Format assumption is verified against real rows in M6 (Task 6.9).
    """
    if not blob:
        return None
    i = blob.find(b"NSString")
    if i < 0:
        return None
    j = i + len(b"NSString") + 5
    if j >= len(blob):
        return None
    n = blob[j]
    if n == 0x81:
        n, j = int.from_bytes(blob[j + 1:j + 3], "little"), j + 3
    elif n == 0x82:
        n, j = int.from_bytes(blob[j + 1:j + 5], "little"), j + 5
    else:
        j += 1
    return blob[j:j + n].decode("utf-8", errors="replace")


def apple_unix(v: int | float) -> float:
    """Messages `date` is nanoseconds since 2001 on modern macOS, seconds on old ones."""
    return (v / 1e9 if v > 1e12 else v) + APPLE_EPOCH


def apple_ts(v: int | float) -> str:
    return datetime.fromtimestamp(apple_unix(v), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
