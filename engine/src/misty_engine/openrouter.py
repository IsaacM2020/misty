"""THE one OpenRouter client. Every cloud call in the engine goes through post().

Order inside post(): paid lock -> online -> key -> ZDR -> secret re-scan -> cap check -> send -> map errors -> spend.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterator, Literal
from urllib.parse import urlparse

import httpx

from .clock import Clock

try:                                                    # http2 needs the h2 package; without it the client stays on http/1.1
    import h2  # noqa: F401
    _H2 = True
except ImportError:
    _H2 = False
from .config import Pinned, Settings
from .errors import CapHit, Fatal, NetworkDown, PrivacyViolation, Retryable
from .local_llm import LocalLLM
from .logs import log_event
from .net import NetState
from .scrub import find_secrets
from .spend import SpendMeter, estimate_usd
from .types import Phase, approx_tokens

REAL_HOST = "openrouter.ai"

# A call Isaac asked for himself in the app (e.g. "Test saved key") passes the paid lock, only inside this scope.
_USER_APPROVED: ContextVar[bool] = ContextVar("misty_user_approved", default=False)


@contextmanager
def user_approved() -> Iterator[None]:
    token = _USER_APPROVED.set(True)
    try:
        yield
    finally:
        _USER_APPROVED.reset(token)


def with_zdr(body: dict) -> dict:
    out = dict(body)
    prov = dict(out.get("provider") or {})
    if prov.get("zdr") is False:
        raise PrivacyViolation("ZDR_DISABLED")
    prov["zdr"] = True
    prov["data_collection"] = "deny"
    out["provider"] = prov
    return out


def _strings(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def assert_clean(body: dict) -> None:
    """Belt and braces: nothing secret-shaped may be in an outgoing payload, ever."""
    for s in _strings(body):
        if s.startswith("data:image/"):                 # a screenshot: base64 can look secret-shaped, and isn't text to scrub
            continue
        if find_secrets(s, "file"):
            raise PrivacyViolation("UNSCRUBBED_CONTENT")


async def _read_stream(resp: httpx.Response, on_delta: Callable[[str], Awaitable[None]],
                       mark: Callable[[str], None] | None = None) -> dict:
    """Server-sent events from /chat/completions, folded back into one ordinary reply.
    `mark` (timing only, Season 3 Day 1): told "first_line" and "first_token" the moment each arrives."""
    import json
    text: list[str] = []
    seen_line = seen_token = False
    calls: dict[int, dict] = {}
    finish, usage = "", {}
    async for line in resp.aiter_lines():
        if not line.startswith("data:"):
            continue                                          # ": OPENROUTER PROCESSING" keep-alives, blank lines
        if mark and not seen_line:
            seen_line = True
            mark("first_line")
        chunk = line[5:].strip()
        if chunk == "[DONE]":
            break
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        if obj.get("error"):
            raise Retryable("STREAM_ERROR")
        usage = obj.get("usage") or usage
        for ch in obj.get("choices") or []:
            d = ch.get("delta") or {}
            if mark and not seen_token and (d.get("content") or d.get("tool_calls")):
                seen_token = True
                mark("first_token" if d.get("content") else "first_tool_token")
            if d.get("content"):
                text.append(d["content"])
                await on_delta(d["content"])
            for tc in d.get("tool_calls") or []:
                c = calls.setdefault(int(tc.get("index") or 0), {"id": "", "type": "function",
                                                                 "function": {"name": "", "arguments": ""}})
                c["id"] = tc.get("id") or c["id"]
                f = tc.get("function") or {}
                c["function"]["name"] += f.get("name") or ""
                c["function"]["arguments"] += f.get("arguments") or ""
            finish = ch.get("finish_reason") or finish
    msg: dict = {"role": "assistant", "content": "".join(text)}
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return {"choices": [{"message": msg, "finish_reason": finish}], "usage": usage}


@dataclass
class ChatResult:
    text: str
    usage: dict
    cost_usd: float
    finish_reason: str = ""


class OpenRouterClient:
    def __init__(self, *, pinned: Pinned, settings: Settings, spend: SpendMeter, net: NetState,
                 key_provider: Callable[[], str | None], clock: Clock,
                 http: httpx.AsyncClient | None = None, base_url: str | None = None,
                 local_http: httpx.AsyncClient | None = None):
        self.pinned, self.settings, self.spend, self.net = pinned, settings, spend, net
        self.local = LocalLLM(settings=settings, spend=spend, clock=clock, http=local_http)
        self.key_provider, self.clock = key_provider, clock
        self.base_url = (base_url or os.environ.get("MISTY_OPENROUTER_BASE") or pinned.openrouter.base_url).rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), limits=httpx.Limits(keepalive_expiry=300), http2=_H2)

    # ---- the choke point
    async def post(self, path: str, body: dict, *, job_id: str | None, phase: Phase,
                   build_id: str | None, estimate_usd: float, model: str, purpose: str | None = None,
                   on_delta: Callable[[str], Awaitable[None]] | None = None,
                   mark: Callable[[str], None] | None = None, zdr: bool = True) -> dict:
        """`mark` is for timing only (Season 3 Day 1): it is told "http_start", "headers", "first_line", "first_token".
        With `on_delta`, the reply streams: each new piece of text goes to on_delta as it arrives, and the return
        value is the same shape as a normal reply (choices[0].message with content and tool_calls, plus usage)."""
        host = urlparse(self.base_url).hostname
        if host == REAL_HOST and self.settings.get("paid_calls_allowed") is not True and not _USER_APPROVED.get():
            raise Fatal("PAID_LOCKED")
        if _USER_APPROVED.get():
            log_event("PAID_USER_APPROVED", model=model)
        if not self.net.online:
            raise NetworkDown()
        key = self.key_provider()
        if not key:
            raise Fatal("NO_KEY")
        if zdr:
            payload = with_zdr(body)
        else:
            # Dev only (Isaac 2026-11-20): ZDR is waived until public release, for free models with no ZDR route such as
            # the Perplexity Decider. One setting turns every such call off; it must be off before Misty ships to anyone.
            if self.settings.get("allow_no_zdr") is not True:
                raise PrivacyViolation("ZDR_REQUIRED")
            payload = dict(body)
            log_event("NO_ZDR_CALL", model=model)
        assert_clean(payload)
        decision = self.spend.check(estimate_usd, phase, build_id, purpose=purpose)
        if not decision.ok:
            raise CapHit(decision.reason or "CAP")

        t0 = time.perf_counter()
        headers = {"Authorization": f"Bearer {key}", "HTTP-Referer": self.pinned.openrouter.referer,
                   "X-Title": self.pinned.openrouter.title}
        try:
            if on_delta is None:
                url = path if path.startswith("https://") else self.base_url + path
                resp = await self._http.post(url, json=payload, headers=headers)
                data = self._interpret(resp)
            else:
                payload = {**payload, "stream": True, "usage": {"include": True}}
                if mark:
                    mark("http_start")
                async with self._http.stream("POST", self.base_url + path, json=payload, headers=headers) as resp:
                    if mark:
                        mark("headers")
                    if resp.status_code != 200:
                        await resp.aread()
                        self._interpret(resp)                 # raises the mapped error
                    data = await _read_stream(resp, on_delta, mark)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError):
            self.net.set_online(False, "client")
            raise NetworkDown()
        except (httpx.TimeoutException):
            raise Retryable("TIMEOUT")
        except httpx.HTTPError:
            raise Retryable("HTTP_ERROR")

        ms = int((time.perf_counter() - t0) * 1000)
        usd = float((data.get("usage") or {}).get("cost") or estimate_usd)
        self.spend.record(provider="openrouter", model=model, job_id=job_id, build_id=build_id, usd=usd,
                          purpose=purpose)
        u = data.get("usage") or {}
        cached = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        log_event("OR_CALL", job_id=job_id, model=model, ms=ms, usd=usd, http=resp.status_code,
                  **({"tokens_in": int(u.get("prompt_tokens") or 0), "cached": cached} if u.get("prompt_tokens") else {}))
        return data

    @staticmethod
    def _interpret(resp: httpx.Response) -> dict:
        s = resp.status_code
        if s == 200:
            try:
                return resp.json()
            except ValueError:
                raise Retryable("BAD_JSON")
        meta: dict = {}
        try:
            meta = (resp.json().get("error") or {}).get("metadata") or {}
        except Exception:
            pass
        if s == 401:
            raise Fatal("KEY_INVALID")
        if s in (402, 403):
            raise Fatal("CREDIT_LIMIT" if (s == 402 or meta.get("limit_source")) else "KEY_INVALID")
        if s == 404:
            raise Fatal("NO_ZDR_ROUTE")
        if s in (408, 429) or s >= 500:
            raise Retryable(f"HTTP_{s}")
        raise Fatal(f"HTTP_{s}")

    # ---- conveniences
    async def try_local(self, *, purpose: str, messages: list[dict], max_tokens: int, job_id: str | None,
                        build_id: str | None = None, temperature: float = 0.2, json_out: bool = False,
                        accept: Callable[[str], bool] | None = None) -> ChatResult | None:
        """The local model (local_llm.py) for a background purpose, or None: the caller then makes its normal cloud call.
        `accept` is the caller's own parse/validation of the text; a reply it rejects counts as a local failure."""
        r = await self.local.chat(purpose=purpose, messages=messages, max_tokens=max_tokens, temperature=temperature,
                                  job_id=job_id, build_id=build_id, json_out=json_out, accept=accept)
        if r is None:
            return None
        return ChatResult(text=r.text, usage=r.usage, cost_usd=0.0, finish_reason=r.finish_reason)

    async def chat(self, *, model_key: Literal["haiku", "strong", "cheap"] = "haiku", messages: list[dict],
                   max_tokens: int, job_id: str | None, phase: Phase, build_id: str | None = None,
                   temperature: float = 0.2, purpose: str = "compile", json_out: bool = False,
                   accept: Callable[[str], bool] | None = None) -> ChatResult:
        if model_key == "haiku" and self.local.routes(purpose):    # a request for a stronger model is never downgraded
            got = await self.try_local(purpose=purpose, messages=messages, max_tokens=max_tokens, job_id=job_id,
                                       build_id=build_id, temperature=temperature, json_out=json_out, accept=accept)
            if got is not None:
                return got
        m = self.pinned.models
        model, order, reasoning = {
            "haiku": (m.haiku, m.haiku_provider_order, m.haiku_reasoning),
            "strong": (m.strong, m.strong_provider_order, m.strong_reasoning),
            "cheap": (m.answer_cheap, m.cheap_provider_order, m.cheap_reasoning),
        }[model_key]
        if not model:
            raise Fatal("MODEL_NOT_PINNED")
        # A model that must reason spends tokens on it before the answer: leave room, or the answer is cut off (P27).
        room = 2000 if "enabled" not in reasoning else 0
        body = {
            "model": model, "messages": messages, "max_tokens": max_tokens + room, "temperature": temperature,
            "provider": {"order": list(order), "allow_fallbacks": True},
            "reasoning": dict(reasoning),
        }
        in_tok = sum(approx_tokens(s) for s in _strings(messages))
        price_key = "cheap" if model_key == "cheap" else model_key
        est = estimate_usd(self.pinned, price_key, in_tok, max_tokens + room)
        data = await self.post(self.pinned.openrouter.chat_path, body, job_id=job_id, phase=phase,
                               build_id=build_id, estimate_usd=est, model=model, purpose=purpose)
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise Retryable("BAD_RESPONSE")
        usage = data.get("usage") or {}
        return ChatResult(text=text, usage=usage, cost_usd=float(usage.get("cost") or est),
                          finish_reason=str(choice.get("finish_reason") or ""))

    async def systemone(self, body: dict, *, job_id: str | None, phase: Phase,
                        build_id: str | None = None, est_in_tokens: int, purpose: str = "jev") -> dict:
        est = estimate_usd(self.pinned, "jev", est_in_tokens, 0)
        return await self.post(self.pinned.openrouter.systemone_path, body, job_id=job_id, phase=phase,
                               build_id=build_id, estimate_usd=est, model=self.pinned.models.jev, purpose=purpose)

    async def decide(self, body: dict, *, purpose: str) -> dict:
        """OpenRouter's alpha Decisions API (free Perplexity Decider models). Not ZDR: needs `allow_no_zdr`."""
        u = urlparse(self.base_url)
        return await self.post(f"{u.scheme}://{u.netloc}/api/alpha/decisions", body, job_id=None, phase="live",
                               build_id=None, estimate_usd=0.0, model=str(body.get("model")), purpose=purpose, zdr=False)

    async def probe(self) -> bool:
        """Cheap reachability check used only to notice reconnects. No body, no key."""
        u = urlparse(self.base_url)
        try:
            await self._http.head(f"{u.scheme}://{u.netloc}/", timeout=5.0)
            return True
        except httpx.HTTPError:
            return False
