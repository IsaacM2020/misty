"""E3: the one small door every teaching model call goes through (purpose `teach`, sub-cap in purpose_caps_daily).

Haiku with thinking is a direct post, the same call shape as tutor.solve. The stronger model goes through ApiRunner.run.
Both end in OpenRouterClient.post, so test_no_bypass stays green. Nothing here knows a subject or a person.
"""
from __future__ import annotations

import json
import re

from ..spend import estimate_usd

THINK_TOKENS = 3500


def parse_json(text: str) -> dict:
    """The first {...} in a model reply. Raises ValueError when there is none."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON")
    out = json.loads(m.group(0))
    if not isinstance(out, dict):
        raise ValueError("not an object")
    return out


async def think(ctx, system: str, user: str, max_tokens: int = 4000, thinking: bool = True, model_key: str = "haiku",
                job_id: str | None = None, temperature: float = 0.1) -> tuple[str, float]:
    """(reply text, dollars). `max_tokens` is for the answer; the thinking budget is added on top."""
    if model_key == "strong":
        res = await ctx.runner.run(system, [user], "teach", purpose="teach", model_key="strong", max_tokens=max_tokens, job_id=job_id)
        return res.text, float(res.cost_usd)
    m = ctx.pinned.models
    reasoning = {"enabled": True, "max_tokens": THINK_TOKENS} if thinking else dict(m.haiku_reasoning)
    total = max_tokens + (THINK_TOKENS if thinking else 0)
    body = {"model": m.haiku, "messages": [{"role": "system", "content": system.strip()}, {"role": "user", "content": user}],
            "max_tokens": total, "temperature": temperature,
            "provider": {"order": list(m.haiku_provider_order), "allow_fallbacks": True}, "reasoning": reasoning}
    est = estimate_usd(ctx.pinned, "haiku", (len(system) + len(user)) // 4 + 200, total)
    data = await ctx.openrouter.post(ctx.pinned.openrouter.chat_path, body, job_id=job_id, phase="live", build_id=None,
                                     estimate_usd=est, model=m.haiku, purpose="teach")
    usd = float((data.get("usage") or {}).get("cost") or est)
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        text = ""
    return text, usd
