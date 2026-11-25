"""Background model calls on Isaac's Mac Mini (llama.cpp `llama-server`, OpenAI-style), with the cloud as the fallback.

One seam: OpenRouterClient.chat() and .try_local() ask `LocalLLM.chat()` first for the purposes in the `local_llm`
setting. It answers a ChatResult-shaped LocalResult, or None, and None always means "do the normal cloud call".
Nothing here raises to the caller. Off by default (`enabled` false in config.DEFAULTS).

Rules (see docs in the setting):
  * Only the purposes listed in the setting go local, and never any in NEVER_LOCAL (live chat, voice, anything he waits on).
  * One request at a time (the server has one useful slot): a lock; waiting longer than `timeout_s` means cloud.
  * Any failure (connection, timeout, HTTP error, empty or cut-off reply, a reply the caller's own parser rejects) falls
    back to the cloud for that request. 3 failures in a row switch local off for 10 minutes (the breaker).
  * Spend: a row with provider 'local', usd 0, same purpose, so the table still shows what ran where.

Thinking: Inkling 2 Mini is a reasoning model. Asking it nicely does not stop that (`reasoning_effort: none`, `reasoning_budget: 0`,
`enable_thinking: false` all still think for 100 to 300 tokens). What does work is to END the message list with an
assistant turn that already has text in it: llama-server then continues that turn in plain-text mode and never opens a
thinking block. We use "{" for JSON replies and a newline for plain text; the reply comes back starting with that text.
`chat_template_kwargs.reasoning_effort = none` is sent as well, as a second layer.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .clock import Clock
from .config import Settings
from .logs import log_event
from .spend import SpendMeter

# Whatever he is waiting on, or speaks to, never goes local, even if someone lists it in the setting.
NEVER_LOCAL = frozenset({
    "chat", "stream", "addressed", "addressed_decider", "jev", "tutor", "lesson", "brief", "class", "hear",
    "ask", "teach", "setup", "prep", "follow", "eval",
})

BREAKER_FAILS = 3
BREAKER_SECONDS = 600
CTX_MARGIN = 128          # tokens left for the template and the server's own overhead
MIN_OUT = 256


@dataclass
class LocalResult:
    text: str
    usage: dict
    finish_reason: str = ""
    ms: int = 0


@dataclass
class _Breaker:
    fails: int = 0
    open_until: float = 0.0


class LocalFailure(Exception):
    """Internal: carries the short reason a local attempt fell back."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


@dataclass
class LocalLLM:
    settings: Settings
    spend: SpendMeter
    clock: Clock
    http: httpx.AsyncClient | None = None
    _breaker: _Breaker = field(default_factory=_Breaker)
    _lock: asyncio.Lock | None = None

    # ---- config
    def config(self) -> dict:
        from .config import DEFAULTS
        cfg = dict(DEFAULTS["local_llm"])
        try:
            v = self.settings.get("local_llm")
        except Exception:
            v = None
        if isinstance(v, dict):
            cfg.update(v)
        return cfg

    def routes(self, purpose: str | None) -> bool:
        """True when this purpose should be tried locally right now (enabled, listed, never-list, breaker)."""
        if not purpose or purpose in NEVER_LOCAL:
            return False
        cfg = self.config()
        if cfg.get("enabled") is not True or purpose not in (cfg.get("purposes") or []):
            return False
        return True

    def breaker_open(self) -> bool:
        return self._breaker.open_until > self.clock.now()

    # ---- the call
    async def chat(self, *, purpose: str, messages: list[dict], max_tokens: int, temperature: float = 0.1,
                   job_id: str | None = None, build_id: str | None = None, json_out: bool = False,
                   accept: Callable[[str], bool] | None = None) -> LocalResult | None:
        """A LocalResult, or None meaning "use the cloud". Never raises for a local problem."""
        if not self.routes(purpose):
            return None
        if self.breaker_open():
            log_event("LOCAL_CALL", purpose=purpose, ok=False, why="breaker_open")
            return None
        cfg = self.config()
        timeout = float(cfg.get("timeout_s") or 120)
        t0 = time.perf_counter()
        n_in = 0
        try:
            n_in = sum(len(s) for s in _strings(messages)) // 3 + 1          # pessimistic: 3 chars a token
            room = int(cfg.get("ctx") or 16384) - CTX_MARGIN - n_in
            if room < MIN_OUT:
                raise LocalFailure("too_long")
            out_cap = min(int(max_tokens), room)
            lock = self._lock = self._lock or asyncio.Lock()
            try:
                await asyncio.wait_for(lock.acquire(), timeout)
            except asyncio.TimeoutError:
                raise LocalFailure("busy")
            try:
                t_req = time.perf_counter()
                data = await self._post(cfg, messages, out_cap, temperature, json_out, timeout)
            finally:
                lock.release()
            res = self._read(data, json_out, int((time.perf_counter() - t_req) * 1000))
            if res.finish_reason == "length":
                raise LocalFailure("truncated")
            if accept is not None:
                try:
                    ok = bool(accept(res.text))
                except Exception:
                    ok = False
                if not ok:
                    raise LocalFailure("rejected")
        except LocalFailure as e:
            self._failed(e.why, purpose, int((time.perf_counter() - t0) * 1000), n_in)
            return None
        self._breaker.fails = 0
        self.spend.record(provider="local", model=str(cfg.get("model")), job_id=job_id, build_id=build_id, usd=0.0,
                          purpose=purpose)
        log_event("LOCAL_CALL", purpose=purpose, ok=True, ms=res.ms, model=str(cfg.get("model")),
                  tokens_in=int(res.usage.get("prompt_tokens") or 0),
                  tokens_out=int(res.usage.get("completion_tokens") or 0))
        return res

    def _failed(self, why: str, purpose: str, ms: int, n_in: int) -> None:
        # "busy" and "too_long" say nothing about the server's health; the rest do
        if why not in ("busy", "too_long"):
            b = self._breaker
            b.fails += 1
            if b.fails >= BREAKER_FAILS:
                b.open_until = self.clock.now() + BREAKER_SECONDS
                log_event("LOCAL_BREAKER", ok=False, count=b.fails)
        log_event("LOCAL_CALL", purpose=purpose, ok=False, why=why, ms=ms)

    async def _post(self, cfg: dict, messages: list[dict], max_tokens: int, temperature: float, json_out: bool,
                    timeout: float) -> dict:
        body = {
            "model": cfg.get("model"),
            # the closing assistant turn is what keeps the model from thinking (module docstring)
            "messages": [*_plain(messages), {"role": "assistant", "content": "{" if json_out else "\n"}],
            "max_tokens": max_tokens, "temperature": temperature, "stream": False,
            "chat_template_kwargs": {"reasoning_effort": "none"},
        }
        url = str(cfg.get("url") or "").rstrip("/") + "/v1/chat/completions"
        http = self.http or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0))
        if self.http is None:
            self.http = http
        try:
            resp = await asyncio.wait_for(http.post(url, json=body), timeout)
        except asyncio.TimeoutError:
            raise LocalFailure("timeout")
        except httpx.TimeoutException:
            raise LocalFailure("timeout")
        except httpx.HTTPError:
            raise LocalFailure("connection")
        if resp.status_code != 200:
            raise LocalFailure(f"http_{resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise LocalFailure("bad_json")

    @staticmethod
    def _read(data: dict, json_out: bool, ms: int) -> LocalResult:
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LocalFailure("bad_response")
        text = text.strip()
        if not text or text == "{" and json_out:
            raise LocalFailure("empty")
        return LocalResult(text=text, usage=data.get("usage") or {}, finish_reason=str(choice.get("finish_reason") or ""),
                           ms=ms)

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()


def _plain(messages: list[dict]) -> list[dict]:
    """The cloud wants some system prompts as [{type: text, text, cache_control}] parts; the Mini only needs the text."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
        out.append({"role": m["role"], "content": c})
    return out


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def json_object(text: str) -> dict | None:
    """The first-brace-to-last-brace JSON object in a model reply (the same rule ApiRunner and the compile parser use)."""
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b <= a:
        return None
    try:
        d = json.loads(text[a:b + 1])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None
