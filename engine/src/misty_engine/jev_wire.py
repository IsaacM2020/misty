"""Jev System One wire format (pinned in docs/verify/jev.md).

Our questions are `Question(id, text, choices, multi)`. On the wire:
- two-choice yes/no          -> one `noul` question (answer = P(true))
- other single choice        -> one `choice` question, `criteria` = {option: description}
- multi-select               -> one `noul` per option, keyed `<id>__<option>`, selected when P >= threshold
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .errors import Fatal

SELECT_THRESHOLD = 0.5
SEP = "__"

# Short descriptions Jev reads next to each option. Anything not listed gets a generic line.
HINTS: dict[str, str] = {
    "none": "Nothing needs to be done.",
    "reply": "Isaac needs to reply to a person.",
    "calendar_event": "There is an event or meeting that belongs on his calendar.",
    "task_or_deadline": "There is something Isaac must do, or a deadline.",
    "fact_to_remember": "There is a fact worth remembering, but no action.",
    "now": "Needs attention within the hour.",
    "today": "Needs attention today.",
    "this_week": "Needs attention this week.",
    "other": "None of the above.",
    "file": "Find a file or document on his Mac.",
    "photo": "Find a photo or image.",
    "message": "Find a chat message or email.",
    "ask_wiki": "A question about his own life, projects or people that his notes could answer.",
    "action": "Do something for him (send, schedule, write).",
    "cloud": "General knowledge or a task that needs a large model.",
    "any": "No time range is mentioned.",
    "week": "This week or the last few days.",
    "month": "This month.",
    "year": "This year.",
    "older": "A specific older period.",
    "files": "Files and documents.",
    "mail": "Email.",
    "calendar": "Calendar events.",
    "imessage": "iMessage chats.",
    "whatsapp": "WhatsApp chats.",
    "claude_mem": "Notes from his earlier coding sessions.",
    "photos": "The photo library.",
}


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    choices: tuple[str, ...]
    multi: bool = False


@dataclass(frozen=True)
class Answer:
    choice: tuple[str, ...]
    confidence: float


def _is_yes_no(q: Question) -> bool:
    return not q.multi and tuple(q.choices) == ("yes", "no")


def _hint(choice: str, extra: dict[str, str] | None = None) -> str:
    return (extra or {}).get(choice) or HINTS.get(choice, f"The item is about: {choice.replace('_', ' ')}.")


def to_request(model: str, context: str, questions: Sequence[Question], hints: dict[str, str] | None = None) -> dict:
    qs: dict[str, dict] = {}
    for q in questions:
        if q.multi:
            for c in q.choices:
                qs[f"{q.id}{SEP}{c}"] = {
                    "type": "noul",
                    "instructions": f"{q.text} Is this true: {_hint(c, hints)}",
                }
        elif _is_yes_no(q):
            qs[q.id] = {"type": "noul", "instructions": q.text}
        else:
            qs[q.id] = {"type": "choice", "instructions": q.text,
                        "criteria": {c: _hint(c, hints) for c in q.choices}}
    return {"model": model, "state": context, "questions": qs}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def from_response(resp: dict, questions: Sequence[Question]) -> dict[str, Answer]:
    answers = resp.get("answers")
    if not isinstance(answers, dict):
        raise Fatal("JEV_BAD_RESPONSE")
    out: dict[str, Answer] = {}
    for q in questions:
        if q.multi:
            picked: list[str] = []
            confs: list[float] = []
            for c in q.choices:
                a = answers.get(f"{q.id}{SEP}{c}")
                if not isinstance(a, dict) or "noul" not in a:
                    raise Fatal("JEV_BAD_RESPONSE")
                p = _clamp(a["noul"])
                if p >= SELECT_THRESHOLD:
                    picked.append(c)
                confs.append(max(p, 1 - p))
            out[q.id] = Answer(tuple(picked), sum(confs) / len(confs) if confs else 1.0)
            continue
        a = answers.get(q.id)
        if not isinstance(a, dict):
            raise Fatal("JEV_BAD_RESPONSE")
        if _is_yes_no(q):
            if "noul" not in a:
                raise Fatal("JEV_BAD_RESPONSE")
            p = _clamp(a["noul"])
            out[q.id] = Answer(("yes",) if p >= SELECT_THRESHOLD else ("no",), max(p, 1 - p))
        else:
            c = a.get("choice")
            if c not in q.choices:
                raise Fatal("JEV_BAD_RESPONSE")
            conf = a.get("confidence")
            if conf is None:
                probs = a.get("probabilities") or {}
                conf = max(probs.values()) if probs else 1.0
            out[q.id] = Answer((c,), _clamp(conf))
    return out
