"""Name rules for the entity registry: normalise, reject, compatible, canonical. Pure functions, no I/O."""
from __future__ import annotations

import re
import unicodedata

HONORIFICS = frozenset({"mr", "mrs", "ms", "miss", "dr", "prof", "professor", "sir", "madam", "mam",
                        "coach", "uncle", "aunty", "auntie", "bhai", "didi"})
ARTIFACT_TOKENS = frozenset({"real", "chat", "group", "whatsapp", "imessage", "member", "members", "contact"})
CHAT_WORDS = re.compile(r"\b(chat members?|members?|group chat|group)\b", re.I)
_PHONE = re.compile(r"^\+?\d[\d\s().-]{5,}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def core_tokens(s: str, kind: str = "person") -> tuple[str, ...]:
    toks = norm(s).split()
    if kind == "person":
        # honorifics and chat artifacts ("Pranav Asrani Real") are not part of who someone is
        toks = [t for t in toks if t not in HONORIFICS and t not in ARTIFACT_TOKENS]
    return tuple(toks)


def reject_reason(raw, kind, *, chat_title="", chat_kind="", user_names=frozenset()):
    """A code when `raw` is not a thing worth an entity, else None. Codes are safe to log and count."""
    r = (raw or "").strip()
    if _PHONE.match(r):
        return "PHONE"
    if _EMAIL.match(r):
        return "EMAIL"
    n = norm(r)
    if not n or (kind == "person" and not core_tokens(r)):
        return "EMPTY"
    if n.isdigit():
        return "NUMERIC"
    if len(n) == 1:
        return "SINGLE_CHAR"
    if n in user_names:
        return "USER"
    if chat_title and chat_kind == "group" and n == norm(chat_title):
        return "CHAT_TITLE"
    if kind == "person" and CHAT_WORDS.search(r):
        return "CHAT_WORD"
    return None


def compatible(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """People: one name's tokens are a subset of the other's. A one-token side must be the other's first or
    last token (a middle name alone is not an identity)."""
    if not a or not b:
        return False
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    if not set(small) <= set(big):
        return False
    if len(small) == 1:
        return small[0] in (big[0], big[-1])
    return True


def canonical_score(alias: str, n: int, chat_titles: frozenset[str] = frozenset()) -> tuple:
    toks = norm(alias).split()
    artifact = bool(set(toks) & ARTIFACT_TOKENS) or norm(alias) in chat_titles
    return (not artifact, min(len(toks), 4), n, len(alias))
