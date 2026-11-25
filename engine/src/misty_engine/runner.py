"""THE one model runner. Every call to a language model (compile aside, which batches) goes through ApiRunner.run().

OpenRouter, ZDR, a spend purpose on every call. No subprocess, no subscription: a consumer has an API key and nothing
else (P45). Instructions are plain `str` written in code; wiki content must be `WikiText` (made by
WikiStore.llm_text), so a prompt can never be assembled from raw files by accident. Retries with backoff are the job
queue's work (Retryable -> 5, 30, 120 s); a purpose over its sub-cap raises CapHit and the job waits.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, NewType, Sequence

from .errors import Retryable
from .local_llm import json_object
from .logs import log_event
from .types import Phase

WikiText = NewType("WikiText", str)


@dataclass
class RunResult:
    text: str
    json: dict | None
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0


class ApiRunner:
    def __init__(self, client, phase_getter: Callable[[], Phase]):
        self.client, self.phase = client, phase_getter

    def _latest_build(self) -> str | None:
        r = self.client.spend.con.execute("SELECT id FROM builds ORDER BY started_at DESC LIMIT 1").fetchone()
        return r[0] if r else None

    async def run(self, system: str, parts: Sequence[str], schema_name: str, *, purpose: str,
                  model_key: str = "haiku", max_tokens: int = 1500, job_id: str | None = None,
                  build_id: str | None = None, phase: Phase | None = None,
                  accept: Callable[[dict], bool] | None = None) -> RunResult:
        """`accept` (optional) is the caller's own check of the parsed JSON. It only matters for a local-model reply
        (local_llm.py): one that is not a JSON object, or that `accept` refuses, is dropped and the cloud is asked."""
        t0 = time.perf_counter()
        phase = phase or self.phase()          # an eval run is a "build" with its own id, whatever the engine is doing
        if phase == "build" and build_id is None:
            build_id = self._latest_build()      # stage 5 jobs carry no build id; their spend still counts in the build
        msgs = [{"role": "system", "content": system.strip()},
                {"role": "user", "content": "\n\n".join(parts)}]
        # the client re-scans every outgoing string (assert_clean) and forces ZDR; nothing is sent before that
        def _ok(text: str) -> bool:
            d = json_object(text)
            return d is not None and (accept is None or bool(accept(d)))
        res = await self.client.chat(model_key=model_key, messages=msgs, max_tokens=max_tokens,
                                     job_id=job_id, phase=phase, build_id=build_id,
                                     purpose=purpose, temperature=0.1, json_out=True, accept=_ok)
        a, b = res.text.find("{"), res.text.rfind("}")
        data = None
        if a != -1 and b > a:
            try:
                data = json.loads(res.text[a:b + 1])
            except ValueError:
                data = None
        log_event("RUN", schema=schema_name, purpose=purpose, usd=res.cost_usd,
                  ms=round((time.perf_counter() - t0) * 1000))
        if not isinstance(data, dict):
            e = Retryable("JSON_PARSE")
            e.cost_usd, e.truncated = res.cost_usd, res.finish_reason == "length"   # paid for, and maybe cut off
            raise e
        return RunResult(text=res.text, json=data, usage=res.usage, cost_usd=res.cost_usd)
