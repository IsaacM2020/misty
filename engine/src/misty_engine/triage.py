"""Plain code that acts on Jev's answers (spec 8.1). Jev judges; these rules decide."""
from __future__ import annotations

from dataclasses import dataclass

from .jev_wire import Answer

KEEP_BELOW = 0.6               # unsure means keep (a false "no" loses a note, a false "yes" costs a fraction of a cent)
ACTION_MIN_CONF = 0.5
ACTION_TO_KIND = {"reply": "reply", "calendar_event": "calendar", "task_or_deadline": "task",
                  "fact_to_remember": "fact"}


@dataclass(frozen=True)
class Decision:
    note: bool
    matters: bool
    suggestion_kind: str | None
    urgency: str
    area: str | None
    badge: bool


def decide_worth(ans: Answer, keep_below: float = KEEP_BELOW) -> bool:
    """True = compile this item into a note."""
    return ans.choice == ("yes",) or ans.confidence < keep_below


def decide_triage(ans: dict[str, Answer], keep_below: float = KEEP_BELOW) -> Decision:
    m = ans["matters"]
    matters = m.choice == ("yes",) or m.confidence < keep_below
    act = ans["action"]
    kind = ACTION_TO_KIND.get(act.choice[0]) if act.confidence >= ACTION_MIN_CONF else None
    urgency = ans["urgency"].choice[0]
    area = ans["area"].choice[0]
    return Decision(note=matters, matters=matters, suggestion_kind=kind, urgency=urgency,
                    area=None if area == "other" else area, badge=(urgency == "now" and kind is not None))
