"""A fake OpenRouter on top of pytest-httpserver: /systemone (Jev) and /chat/completions (Haiku).

Used by every M3-M5 test, so no test ever makes a paid call.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from werkzeug.wrappers import Response

SEP = "__"
ITEM_RE = re.compile(r'<item id="([^"]+)"')


def default_draft(item_id: str) -> dict:
    return {"item_id": item_id, "title": f"Note for {item_id}", "summary": f"Summary of {item_id}. It has text.",
            "key_facts": [f"fact about {item_id}"], "people": [], "projects": [], "topics": [],
            "area_hint": "", "do": f"Do the thing for {item_id}", "dates": []}


class FakeOpenRouter:
    def __init__(self, httpserver):
        self.server = httpserver
        self.answers: dict[str, tuple[Any, float]] = {}
        self.systemone_bodies: list[dict] = []
        self.chat_bodies: list[dict] = []
        self.drafts: dict[str, dict] = {}
        self._chat_queue: list[str] = []
        self._chat_const: str | None = None
        self.finish_reason = "stop"             # set to "length" to simulate output cut off at the cap
        self._fail: list[tuple[str, int]] = []           # (path, status)
        self._cache_written = False
        self.jev_cost: float | None = None       # None = price the call like the real model from pinned.toml
        self.chat_cost: float | None = None
        self.note_tokens = 550                   # completion tokens reported per note (what the estimator assumes)
        from misty_engine.config import load_pinned
        self.prices = load_pinned().prices
        httpserver.expect_request("/api/v1/systemone", method="POST").respond_with_handler(self._systemone)
        httpserver.expect_request("/api/v1/chat/completions", method="POST").respond_with_handler(self._chat)

    # ---- configuration
    def set_answers(self, answers: dict[str, tuple[Any, float]]) -> None:
        """{"worth": ("no", 0.95)} or {"sources": (("files", "mail"), 0.9)}."""
        self.answers.update(answers)

    def chat_returns(self, text: str, times: int = 1) -> None:
        self._chat_queue.extend([text] * times)

    def chat_text(self, text: str | None) -> None:
        self._chat_const = text

    def set_draft(self, item_id: str, **fields) -> None:
        self.drafts.setdefault(item_id, {}).update(fields)

    def fail(self, path: str, status: int, times: int = 1) -> None:
        self._fail.extend([(path, status)] * times)

    # ---- inspection
    def chat_user_texts(self) -> list[str]:
        out = []
        for b in self.chat_bodies:
            for m in b["messages"]:
                if m["role"] == "user":
                    out.append(m["content"] if isinstance(m["content"], str)
                               else " ".join(p.get("text", "") for p in m["content"]))
        return out

    def all_payload_texts(self) -> list[str]:
        return [json.dumps(b) for b in self.systemone_bodies + self.chat_bodies]

    @property
    def request_count(self) -> int:
        return len(self.systemone_bodies) + len(self.chat_bodies)

    @property
    def last_jev_state(self) -> str:
        return self.systemone_bodies[-1]["state"]

    def _jev_usd(self, body: dict) -> float:
        if self.jev_cost is not None:
            return self.jev_cost
        return (len(json.dumps(body["questions"])) // 4 + len(body["state"]) // 4) * self.prices.jev_in / 1e6

    def _maybe_fail(self, path: str) -> Response | None:
        for i, (p, status) in enumerate(self._fail):
            if p == path:
                self._fail.pop(i)
                return Response(json.dumps({"error": {"code": status, "message": "fake"}}), status=status,
                                mimetype="application/json")
        return None

    # ---- handlers
    def _systemone(self, request) -> Response:
        failed = self._maybe_fail("/systemone")
        if failed:
            return failed
        body = json.loads(request.data)
        self.systemone_bodies.append(body)
        answers: dict[str, dict] = {}
        for key, q in body["questions"].items():
            qid, _, opt = key.partition(SEP)
            ov = self.answers.get(qid)
            if q["type"] == "choice":
                options = list(q["criteria"])
                chosen = ov[0] if ov and ov[0] in options else options[0]
                conf = ov[1] if ov else 0.9
                rest = (1 - conf) / max(len(options) - 1, 1)
                answers[key] = {"type": "choice", "choice": chosen, "confidence": conf,
                                "probabilities": {o: (conf if o == chosen else rest) for o in options}}
            elif opt:                                     # one option of a multi-select
                sel, conf = ov if ov else ((), 0.9)
                sel = (sel,) if isinstance(sel, str) else tuple(sel)
                answers[key] = {"type": "noul", "noul": conf if opt in sel else 1 - conf}
            else:                                         # yes/no
                choice, conf = ov if ov else ("yes", 0.9)
                answers[key] = {"type": "noul", "noul": conf if choice == "yes" else 1 - conf}
        resp = {"id": "gen-fake", "model": body["model"] + "-fake", "provider": "TypeSafe", "answers": answers,
                "usage": {"cost": self._jev_usd(body), "input_tokens": len(body["state"]) // 4, "output_tokens": 10}}
        return Response(json.dumps(resp), mimetype="application/json")

    def _chat(self, request) -> Response:
        failed = self._maybe_fail("/chat/completions")
        if failed:
            return failed
        body = json.loads(request.data)
        self.chat_bodies.append(body)
        user = ""
        for m in body["messages"]:
            if m["role"] == "user":
                user = m["content"] if isinstance(m["content"], str) else " ".join(
                    p.get("text", "") for p in m["content"])
        if self._chat_queue:
            text = self._chat_queue.pop(0)
        elif self._chat_const is not None:
            text = self._chat_const
        else:
            notes = []
            for iid in ITEM_RE.findall(user):
                d = default_draft(iid)
                d.update(self.drafts.get(iid, {}))
                notes.append(d)
            text = json.dumps({"notes": notes})
        sys_parts = [m for m in body["messages"] if m["role"] == "system"]
        sys_tokens = sum(len(json.dumps(m["content"])) for m in sys_parts) // 4
        cached = sys_tokens if self._cache_written else 0
        written = 0 if self._cache_written else sys_tokens
        self._cache_written = True
        prompt_tokens = sys_tokens + len(user) // 4
        n_items = len(ITEM_RE.findall(user))
        completion = max(len(text) // 4, n_items * self.note_tokens)
        p = self.prices
        cost = ((prompt_tokens - cached - written) * p.haiku_in + cached * p.haiku_cache_read
                + written * p.haiku_cache_write + completion * p.haiku_out) / 1e6
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion,
                 "cost": self.chat_cost if self.chat_cost is not None else cost,
                 "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written}}
        resp = {"id": "gen-fake", "model": body["model"], "provider": "Google", "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": self.finish_reason, "message": {"role": "assistant", "content": text}}],
                "usage": usage}
        return Response(json.dumps(resp), mimetype="application/json")


FakeAnswerFn = Callable[[str], dict]
