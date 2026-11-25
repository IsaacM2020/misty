"""The local (Mac Mini) model for background jobs: routing, fallback, breaker, lock, spend. No network: fake HTTP only."""
import asyncio
import json
import logging

import httpx
import pytest

from misty_engine.local_llm import BREAKER_SECONDS, NEVER_LOCAL
from misty_engine.net import NetState
from misty_engine.openrouter import OpenRouterClient
from misty_engine.runner import ApiRunner
from misty_engine.errors import Retryable
from misty_engine.spend import SpendMeter

MSG = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
CLOUD_TEXT = "CLOUD"
LOCAL_TEXT = "local text"


def _local_reply(text=LOCAL_TEXT, finish="stop"):
    return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": finish,
                                                  "message": {"role": "assistant", "content": text}}],
                                     "usage": {"prompt_tokens": 12, "completion_tokens": 5}})


class Rig:
    """An OpenRouterClient whose cloud and local HTTP are both fake, counting what reaches each."""

    def __init__(self, con, settings, clock, pinned, local_handler=None):
        self.local_calls, self.cloud_calls, self.local_bodies = 0, 0, []
        self.local_handler = local_handler or (lambda req: _local_reply())

        async def local(req):
            self.local_calls += 1
            self.local_bodies.append(json.loads(req.content))
            r = self.local_handler(req)
            return await r if asyncio.iscoroutine(r) else r

        def cloud(req):
            self.cloud_calls += 1
            return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop",
                                                          "message": {"role": "assistant", "content": CLOUD_TEXT}}],
                                             "usage": {"prompt_tokens": 9, "completion_tokens": 1, "cost": 0.001}})
        self.client = OpenRouterClient(
            pinned=pinned, settings=settings, spend=SpendMeter(con, clock, settings), net=NetState(),
            key_provider=lambda: "sk-or-v1-test", clock=clock, base_url="http://127.0.0.1:9/api/v1",
            http=httpx.AsyncClient(transport=httpx.MockTransport(cloud)),
            local_http=httpx.AsyncClient(transport=httpx.MockTransport(local)))
        self.con, self.settings, self.clock = con, settings, clock

    async def chat(self, purpose="summarise", **kw):
        return await self.client.chat(model_key=kw.pop("model_key", "haiku"), messages=kw.pop("messages", MSG),
                                      max_tokens=kw.pop("max_tokens", 200), job_id="jb_0000000000000001", phase="live",
                                      purpose=purpose, **kw)

    def enable(self, **over):
        cfg = {"enabled": True, "url": "http://mini.test:8080", "model": "inkling-2-mini",
               "purposes": ["summarise", "compile", "chat_compact"], "timeout_s": 5, "ctx": 16384, **over}
        self.settings.set("local_llm", cfg)


@pytest.fixture
def rig(con, settings, clock, pinned):
    return Rig(con, settings, clock, pinned)


# ---- off by default / routing

def test_default_is_disabled(settings):
    from misty_engine.config import DEFAULTS
    assert DEFAULTS["local_llm"]["enabled"] is False
    assert DEFAULTS["local_llm"]["purposes"] == ["summarise", "compile", "chat_compact"]
    assert settings.get("local_llm")["enabled"] is False


async def test_disabled_setting_leaves_behaviour_untouched(rig):
    r = await rig.chat("summarise")
    assert r.text == CLOUD_TEXT and rig.local_calls == 0 and rig.cloud_calls == 1
    assert rig.con.execute("SELECT COUNT(*) FROM spend WHERE provider='local'").fetchone()[0] == 0


async def test_listed_purpose_goes_local_and_records_free_spend(rig):
    rig.enable()
    r = await rig.chat("compile")
    assert r.text == LOCAL_TEXT and r.cost_usd == 0.0 and rig.cloud_calls == 0
    row = rig.con.execute("SELECT provider, model, usd, purpose FROM spend").fetchone()
    assert tuple(row) == ("local", "inkling-2-mini", 0.0, "compile")


@pytest.mark.parametrize("purpose", ["summarise", "compile", "chat_compact"])
async def test_default_purposes_route(rig, purpose):
    rig.enable()
    assert (await rig.chat(purpose)).text == LOCAL_TEXT


@pytest.mark.parametrize("purpose", ["judge", "priorities", "about", "lesson", "nonsense"])
async def test_unlisted_purposes_stay_in_the_cloud(rig, purpose):
    rig.enable()
    assert (await rig.chat(purpose)).text == CLOUD_TEXT and rig.local_calls == 0


@pytest.mark.parametrize("purpose", sorted(NEVER_LOCAL | {"chat", "stream", "addressed", "jev", "tutor", "lesson",
                                                          "brief", "class"}))
async def test_live_and_voice_purposes_never_go_local_even_if_listed(rig, purpose):
    rig.enable(purposes=[purpose, "summarise"])
    assert (await rig.chat(purpose)).text == CLOUD_TEXT and rig.local_calls == 0


async def test_a_stronger_model_request_is_never_downgraded(rig):
    rig.enable()
    assert (await rig.chat("summarise", model_key="cheap")).text == CLOUD_TEXT and rig.local_calls == 0


async def test_request_shape_stops_thinking_and_is_capped(rig):
    rig.enable()
    await rig.chat("summarise", json_out=True, max_tokens=300)
    b = rig.local_bodies[0]
    assert b["model"] == "inkling-2-mini" and b["max_tokens"] == 300 and b["stream"] is False
    assert b["messages"][-1] == {"role": "assistant", "content": "{"}          # the prefill that skips thinking
    assert b["chat_template_kwargs"] == {"reasoning_effort": "none"}
    await rig.chat("chat_compact")
    assert rig.local_bodies[1]["messages"][-1] == {"role": "assistant", "content": "\n"}


async def test_cache_control_parts_are_flattened_to_plain_text_for_the_mini(rig):
    rig.enable()
    parts = [{"role": "system", "content": [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]},
             {"role": "user", "content": "u"}]
    await rig.chat("compile", messages=parts)
    assert rig.local_bodies[0]["messages"][:2] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "u"}]


async def test_json_prefill_is_not_a_result_on_its_own(rig):
    rig.enable()
    rig.local_handler = lambda req: _local_reply("{")
    assert (await rig.chat("compile", json_out=True)).text == CLOUD_TEXT


# ---- fallback, one test per failure kind

def _boom(exc):
    def h(req):
        raise exc
    return h


FAILURES = {
    "connection": lambda: _boom(httpx.ConnectError("no route")),
    "read_timeout": lambda: _boom(httpx.ReadTimeout("slow")),
    "http_500": lambda: (lambda req: httpx.Response(500, text="oops")),
    "http_400": lambda: (lambda req: httpx.Response(400, text="bad")),
    "empty": lambda: (lambda req: _local_reply("   ")),
    "no_choices": lambda: (lambda req: httpx.Response(200, json={"choices": []})),
    "bad_json": lambda: (lambda req: httpx.Response(200, text="<html>")),
    "truncated": lambda: (lambda req: _local_reply("cut off", finish="length")),
}


@pytest.mark.parametrize("kind", sorted(FAILURES))
async def test_each_failure_kind_falls_back_to_the_cloud(rig, kind):
    rig.enable()
    rig.local_handler = FAILURES[kind]()
    r = await rig.chat("summarise")
    assert r.text == CLOUD_TEXT and rig.local_calls == 1 and rig.cloud_calls == 1
    assert rig.con.execute("SELECT COUNT(*) FROM spend WHERE provider='local'").fetchone()[0] == 0
    assert rig.con.execute("SELECT COUNT(*) FROM spend WHERE provider='openrouter'").fetchone()[0] == 1


async def test_a_slow_server_times_out_and_falls_back(rig):
    rig.enable(timeout_s=0.05)

    async def slow(req):
        await asyncio.sleep(1)
        return _local_reply()
    rig.local_handler = slow
    assert (await rig.chat("summarise")).text == CLOUD_TEXT and rig.cloud_calls == 1


async def test_a_reply_the_caller_rejects_falls_back(rig):
    rig.enable()
    r = await rig.chat("compile", accept=lambda t: t == "something else")
    assert r.text == CLOUD_TEXT and rig.local_calls == 1 and rig.cloud_calls == 1
    r = await rig.chat("compile", accept=lambda t: t == LOCAL_TEXT)
    assert r.text == LOCAL_TEXT


async def test_an_accept_that_raises_counts_as_a_rejection(rig):
    rig.enable()

    def bad(t):
        raise KeyError("x")
    assert (await rig.chat("compile", accept=bad)).text == CLOUD_TEXT


async def test_an_input_too_big_for_the_server_context_goes_to_the_cloud_without_asking(rig):
    rig.enable(ctx=1000)
    r = await rig.chat("summarise", messages=[{"role": "user", "content": "word " * 2000}])
    assert r.text == CLOUD_TEXT and rig.local_calls == 0


async def test_output_cap_is_clamped_to_the_context(rig):
    rig.enable(ctx=2000)
    await rig.chat("summarise", messages=[{"role": "user", "content": "x" * 3000}], max_tokens=8000)
    assert rig.local_bodies[0]["max_tokens"] < 8000


# ---- the breaker

async def test_breaker_trips_after_three_failures_and_resets_after_ten_minutes(rig):
    rig.enable()
    rig.local_handler = FAILURES["http_500"]()
    for _ in range(3):
        await rig.chat("summarise")
    assert rig.local_calls == 3
    r = await rig.chat("summarise")                       # open: not even tried
    assert r.text == CLOUD_TEXT and rig.local_calls == 3 and rig.client.local.breaker_open()
    rig.clock.advance(BREAKER_SECONDS - 1)
    await rig.chat("summarise")
    assert rig.local_calls == 3                           # still inside the 10 minutes
    rig.clock.advance(2)
    rig.local_handler = lambda req: _local_reply()
    assert (await rig.chat("summarise")).text == LOCAL_TEXT and rig.local_calls == 4
    assert not rig.client.local.breaker_open()


async def test_a_failed_probe_after_the_pause_reopens_it_at_once(rig):
    rig.enable()
    rig.local_handler = FAILURES["connection"]()
    for _ in range(3):
        await rig.chat("summarise")
    rig.clock.advance(BREAKER_SECONDS + 1)
    await rig.chat("summarise")
    assert rig.local_calls == 4 and rig.client.local.breaker_open()
    await rig.chat("summarise")
    assert rig.local_calls == 4


async def test_a_success_resets_the_failure_count(rig):
    rig.enable()
    rig.local_handler = FAILURES["http_500"]()
    await rig.chat("summarise")
    await rig.chat("summarise")
    rig.local_handler = lambda req: _local_reply()
    await rig.chat("summarise")                           # success: count back to zero
    rig.local_handler = FAILURES["http_500"]()
    await rig.chat("summarise")
    await rig.chat("summarise")
    assert not rig.client.local.breaker_open()            # only 2 in a row


async def test_rejected_replies_count_toward_the_breaker(rig):
    rig.enable()
    for _ in range(3):
        await rig.chat("compile", accept=lambda t: False)
    assert rig.client.local.breaker_open()


# ---- one at a time

async def test_only_one_request_reaches_the_server_at_a_time(rig):
    rig.enable()
    live = {"now": 0, "max": 0}

    async def h(req):
        live["now"] += 1
        live["max"] = max(live["max"], live["now"])
        await asyncio.sleep(0.02)
        live["now"] -= 1
        return _local_reply()
    rig.local_handler = h
    out = await asyncio.gather(*(rig.chat("summarise") for _ in range(4)))
    assert [r.text for r in out] == [LOCAL_TEXT] * 4 and live["max"] == 1 and rig.cloud_calls == 0


async def test_waiting_longer_than_the_timeout_uses_the_cloud_and_is_not_a_server_failure(rig):
    rig.enable(timeout_s=0.05)
    rig.client.local._lock = asyncio.Lock()
    await rig.client.local._lock.acquire()                # someone else holds the one slot
    for _ in range(4):
        r = await rig.chat("summarise")
        assert r.text == CLOUD_TEXT
    assert rig.local_calls == 0 and not rig.client.local.breaker_open()


# ---- the callers' own parsing

async def test_runner_falls_back_when_local_is_not_json(rig):
    rig.enable()
    rig.local_handler = lambda req: _local_reply("sure! here is a summary")
    # the cloud fake answers plain text too, so the runner raises its normal parse error: proof the cloud was asked
    with pytest.raises(Retryable):
        await ApiRunner(rig.client, lambda: "live").run("s", ["p"], "x", purpose="summarise")
    assert rig.local_calls == 1 and rig.cloud_calls == 1


async def test_runner_takes_a_good_local_json_reply_and_accept_can_refuse_it(rig):
    rig.enable()
    rig.local_handler = lambda req: _local_reply('{"entities": []}')   # the server echoes the "{" prefill with the reply
    out = await ApiRunner(rig.client, lambda: "live").run("s", ["p"], "x", purpose="summarise")
    assert out.json == {"entities": []} and out.cost_usd == 0.0 and rig.cloud_calls == 0
    with pytest.raises(Retryable):                        # refused locally, then the cloud's plain text fails to parse
        await ApiRunner(rig.client, lambda: "live").run("s", ["p"], "x", purpose="summarise", accept=lambda d: False)
    assert rig.cloud_calls == 1


async def test_try_local_returns_none_when_not_routed(rig):
    assert await rig.client.try_local(purpose="summarise", messages=MSG, max_tokens=50, job_id=None) is None
    rig.enable()
    assert (await rig.client.try_local(purpose="chat", messages=MSG, max_tokens=50, job_id=None)) is None
    assert (await rig.client.try_local(purpose="chat_compact", messages=MSG, max_tokens=50, job_id=None)).text == LOCAL_TEXT


# ---- the log

async def test_local_call_line_is_logged_with_content_free_fields(rig, caplog):
    rig.enable()
    with caplog.at_level(logging.INFO, logger="misty"):
        await rig.chat("summarise")
        rig.local_handler = FAILURES["http_500"]()
        await rig.chat("summarise")
    lines = [r.misty_fields for r in caplog.records if r.msg == "LOCAL_CALL"]
    assert lines[0]["ok"] is True and lines[0]["purpose"] == "summarise" and lines[0]["tokens_out"] == 5 and "ms" in lines[0]
    assert lines[1]["ok"] is False and lines[1]["why"] == "http_500"
