"""Who is on the other side of a chat, and who sent a mail: small shared helpers for item meta (Task 3.1).

Handles identify people across sources (a phone number is the same person in iMessage and WhatsApp). They go into
item meta and the registry only: never into a log line and never into a model prompt."""
from __future__ import annotations

import re

_PHONE_SHAPED = re.compile(r"^\+?[\d\s().-]{6,}$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def phone_shaped(text: str | None) -> bool:
    return bool(text) and bool(_PHONE_SHAPED.match(text.strip()))


def phone_handle(raw: str) -> list[str] | None:
    """'6591234567@s.whatsapp.net', '+65 9123 4567', 'iMessage;-;+6591234567' -> ['phone', '+6591234567']."""
    digits = re.sub(r"\D", "", raw.split("@")[0].rsplit(";", 1)[-1])
    return ["phone", "+" + digits] if 7 <= len(digits) <= 15 else None


def email_handle(raw: str | None) -> list[str] | None:
    m = _EMAIL.search(raw or "")
    return ["email", m.group(0).lower()] if m else None


def counterpart(name: str | None, handle: list[str] | None, origin: str) -> dict:
    """`name` is None when we only know a number. `origin` says where the name came from: contacts or chat."""
    n = (name or "").strip()
    return {"name": None if (not n or phone_shaped(n) or _EMAIL.fullmatch(n)) else n, "handle": handle,
            "from": origin}
