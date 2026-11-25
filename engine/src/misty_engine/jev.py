"""Jev (TypeSafe System One) client: yes/no and multiple-choice judgements on scrubbed excerpts."""
from __future__ import annotations

from typing import Callable, Sequence

from .jev_wire import Answer, Question, from_response, to_request
from .types import Phase, approx_tokens

WORTH = [Question(
    "worth",
    "Is this item worth turning into a note in a personal knowledge base about Isaac "
    "(his schoolwork, projects, people, plans, decisions)? Spam, receipts, automated notifications, "
    "boilerplate and near-empty items are not.",
    ("yes", "no"))]


def TRIAGE(areas: Sequence[str]) -> list[Question]:
    return [
        Question("matters", "Does this item matter to Isaac's life, work or study?", ("yes", "no")),
        Question("action", "What does Isaac need to do because of this item?",
                 ("none", "reply", "calendar_event", "task_or_deadline", "fact_to_remember")),
        Question("area", "Which life area does this belong to?", tuple(areas) + ("other",)),
        Question("urgency", "How urgent is it?", ("now", "today", "this_week", "none")),
    ]


ROUTE = [
    Question("route", "What does the user want?", ("file", "photo", "message", "ask_wiki", "action", "cloud")),
    Question("sources", "Which sources should be searched?",
             ("files", "mail", "calendar", "imessage", "whatsapp", "claude_mem", "photos"), multi=True),
    Question("when", "What time range is meant?", ("any", "today", "week", "month", "year", "older")),
]


def trim_to_tokens(text: str, max_tokens: int) -> str:
    """Whole-line trimming: keep lines from the top until the budget is used (the head is the most useful part)."""
    if approx_tokens(text) <= max_tokens:
        return text
    out: list[str] = []
    used = 0
    for line in text.split("\n"):
        t = approx_tokens(line) + 1
        if used + t > max_tokens:
            if not out:                       # one giant line: cut it by characters
                return line[: max_tokens * 4]
            break
        out.append(line)
        used += t
    return "\n".join(out)


class JevClient:
    def __init__(self, or_client, pinned, card_provider: Callable[[], str]):
        self.or_client, self.pinned, self.card_provider = or_client, pinned, card_provider

    async def ask(self, context: str, questions: Sequence[Question], *, job_id: str | None, phase: Phase,
                  build_id: str | None = None, purpose: str = "jev",
                  hints: dict[str, str] | None = None) -> dict[str, Answer]:
        card = self.card_provider().strip()
        excerpt = trim_to_tokens(context, self.pinned.jev.max_input_tokens)
        state = f"{card}\n\n{excerpt}" if card else excerpt
        body = to_request(self.pinned.models.jev, state, questions, hints)
        qtext = sum(approx_tokens(q.text) for q in questions)
        resp = await self.or_client.systemone(body, job_id=job_id, phase=phase, build_id=build_id,
                                              est_in_tokens=approx_tokens(state) + qtext + 40 * len(questions),
                                              purpose=purpose)
        return from_response(resp, questions)

    async def worth(self, excerpt: str, *, job_id, phase: Phase, build_id=None) -> Answer:
        return (await self.ask(excerpt, WORTH, job_id=job_id, phase=phase, build_id=build_id))["worth"]

    async def triage(self, excerpt: str, areas: Sequence[str], *, job_id, phase: Phase,
                     build_id=None) -> dict[str, Answer]:
        return await self.ask(excerpt, TRIAGE(areas), job_id=job_id, phase=phase, build_id=build_id)

    async def route(self, query: str, *, job_id, phase: Phase, build_id=None) -> dict[str, Answer]:
        return await self.ask(query, ROUTE, job_id=job_id, phase=phase, build_id=build_id)
