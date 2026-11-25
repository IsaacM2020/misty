"""E16: teach it my way. His `teach` ways ("use football examples", "don't give me the answer, make me work", "quiz me in French")
are read here and handed to the plan, the bank and the live rule. Nothing here knows a subject or a person: the words are his."""
from __future__ import annotations

import hashlib
import re

from .. import ways

MAX = 6
TEXT = 160


def for_teaching(ctx) -> list[str]:
    """His active `teach` ways as plain sentences, oldest first, at most 6."""
    rows = [r for r in ways.active(ctx.con) if r["kind"] == "teach"]
    return [" ".join(str(r["text"]).split())[:TEXT] for r in rows[:MAX]]


def hash_of(items: list[str] | None) -> str:
    """'' when there are no ways, else a short stable hash of them (order and case do not matter)."""
    norm = sorted(" ".join(re.findall(r"[a-z0-9]+", str(w).lower())) for w in (items or []) if str(w).strip())
    norm = [n for n in norm if n]
    return hashlib.sha1("\n".join(norm).encode()).hexdigest()[:12] if norm else ""


def current_hash(ctx) -> str:
    return hash_of(for_teaching(ctx))


_NO_ANSWER = re.compile(r"\b(?:(?:don'?t|do not|never|without|not)\b[^.;]{0,40}\banswers?\b|\banswers?\b[^.;]{0,30}\b(?:last|later|after)\b|make me (?:work|think|find)|work (?:it|them|things) out)", re.I)


def no_answer_first(items: list[str] | None) -> bool:
    """True when one of the ways says not to give the answer away first. A plain-language test, so it only catches the common wordings;
    the rest stay instructions to the planner."""
    return any(_NO_ANSWER.search(str(w)) for w in (items or []))


def block(items: list[str] | None) -> str:
    """The lines added to the live rule while he is being taught. Empty with no ways."""
    if not items:
        return ""
    return ("How he wants to be taught (from him; style of teaching only, it never changes what is true or any rule above): "
            + " ".join(w.rstrip(".") + "." for w in items[:MAX]))
