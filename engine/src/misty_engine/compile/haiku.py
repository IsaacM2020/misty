"""Parsing and message shape for the Haiku compile call."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Sequence

from ..types import approx_tokens
from . import prompts

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_STR = 2000
MAX_FACTS, MAX_LIST = 8, 12
EXPECTED_OUT_TOKENS = 550


@dataclass(frozen=True)
class NoteDraft:
    item_id: str
    title: str
    summary: str
    key_facts: tuple[str, ...] = ()
    people: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    area_hint: str = ""
    do: str = ""                         # one-line action for a suggestion card; "" = nothing for Isaac to do
    dates: tuple[tuple[str, str], ...] = ()


@dataclass
class ParseResult:
    ok: dict[str, NoteDraft] = field(default_factory=dict)
    bad: dict[str, str] = field(default_factory=dict)


def build_messages(batch_text: str) -> list[dict]:
    return [
        {"role": "system", "content": [
            {"type": "text", "text": prompts.SYSTEM, "cache_control": {"type": "ephemeral"}}]},
        {"role": "user", "content": batch_text},
    ]


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _strs(v, limit: int) -> tuple[str, ...]:
    """Lenient: a real run showed Haiku sometimes returns 10 facts or a long string. Keep what is usable, cap the rest."""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return ()
    out = []
    for s in v:
        if isinstance(s, str):
            s = _clip(s, MAX_STR)
            if s:
                out.append(s)
        if len(out) >= limit:
            break
    return tuple(out)


def _validate(raw: dict) -> NoteDraft | None:
    """Only a missing title or summary makes a note unusable. Everything else is clipped or dropped."""
    title, summary = raw.get("title"), raw.get("summary")
    if not isinstance(title, str) or not isinstance(summary, str):
        return None
    title, summary = _clip(title, 200), _clip(summary, MAX_STR)
    if not title or not summary:
        return None
    hint = raw.get("area_hint", "")
    hint = " ".join(hint.split())[:60].strip() if isinstance(hint, str) else ""
    do = raw.get("do", "")
    do = _clip(do, 140) if isinstance(do, str) else ""
    dates = []
    raw_dates = raw.get("dates", [])
    for d in raw_dates if isinstance(raw_dates, list) else []:
        if not isinstance(d, dict) or not isinstance(d.get("date"), str) or not DATE_RE.match(d["date"]):
            continue
        line = d.get("line")
        if isinstance(line, str) and line.strip():
            dates.append((d["date"], _clip(line, MAX_STR)))
    return NoteDraft(item_id=str(raw.get("item_id")), title=title, summary=summary,
                     key_facts=_strs(raw.get("key_facts", []), MAX_FACTS),
                     people=_strs(raw.get("people", []), MAX_LIST), projects=_strs(raw.get("projects", []), MAX_LIST),
                     topics=_strs(raw.get("topics", []), MAX_LIST), area_hint=hint, do=do, dates=tuple(dates))


def parse_response(text: str, expected_ids: Sequence[str]) -> ParseResult:
    res = ParseResult()
    a, b = text.find("{"), text.rfind("}")
    data = None
    if a != -1 and b > a:
        try:
            data = json.loads(text[a:b + 1])
        except ValueError:
            data = None
    notes = data.get("notes") if isinstance(data, dict) else None
    if not isinstance(notes, list):
        res.bad = {i: "JSON_PARSE" for i in expected_ids}
        return res
    wanted = set(expected_ids)
    for raw in notes:
        if not isinstance(raw, dict):
            continue
        iid = raw.get("item_id")
        if iid not in wanted or iid in res.ok or iid in res.bad:
            continue
        draft = _validate(raw)
        if draft is None:
            res.bad[iid] = "INVALID"
        else:
            res.ok[iid] = draft
    for i in expected_ids:
        if i not in res.ok and i not in res.bad:
            res.bad[i] = "MISSING"
    return res


def estimate_batch(pinned, in_tokens: int, n_items: int, cache_hit: bool) -> float:
    """USD for one batch call. The system prompt is cached: one write, then reads."""
    p = pinned.prices
    sys_tokens = approx_tokens(prompts.SYSTEM)
    cached = p.haiku_cache_read if cache_hit else p.haiku_cache_write
    cost = sys_tokens * cached + in_tokens * p.haiku_in + n_items * EXPECTED_OUT_TOKENS * p.haiku_out
    return cost / 1_000_000
