import json

import httpx
import pytest

from misty_engine.errors import CapHit, Fatal, NetworkDown, PrivacyViolation, Retryable
from misty_engine.net import NetState
from misty_engine.openrouter import OpenRouterClient, assert_clean, with_zdr
from misty_engine.spend import SpendMeter

MSG = [{"role": "user", "content": "hi"}]


def test_with_zdr_forces_flag():
    assert with_zdr({"provider": {"order": ["a"]}})["provider"] == {"order": ["a"], "zdr": True, "data_collection": "deny"}
    with pytest.raises(PrivacyViolation):
        with_zdr({"provider": {"zdr": False}})


def test_assert_clean_blocks_secret_anywhere():
    with pytest.raises(PrivacyViolation):
        assert_clean({"messages": [{"role": "user", "content": [{"type": "text", "text": "password: hunter2"}]}]})
    assert_clean({"messages": [{"role": "user", "content": "[REDACTED:password] fine"}]})


async def test_chat_sends_zdr_and_records_spend(client, httpserver, con, chat_ok):
    httpserver.expect_request("/api/v1/chat/completions", method="POST").respond_with_json(chat_ok)
    r = await client.chat(model_key="haiku", messages=MSG, max_tokens=50, job_id="jb_0000000000000001", phase="live")
    req, _ = httpserver.log[0]
    body = json.loads(req.data)
    assert body["provider"]["zdr"] is True and body["provider"]["data_collection"] == "deny"
    assert body["model"] == "anthropic/claude-haiku-5.5" and "usage" not in body
    assert body["reasoning"] == {"enabled": False}
    assert req.headers["Authorization"] == "Bearer sk-or-v1-test"
    assert con.execute("select count(*) from spend").fetchone()[0] == 1
    assert r.text == "OK" and r.cost_usd == pytest.approx(0.0000204)


async def test_unscrubbed_payload_never_sent(client, httpserver):
    with pytest.raises(PrivacyViolation):
        await client.chat(model_key="haiku", messages=[{"role": "user", "content": "AKIAIOSFODNN7EXAMPLE"}],
                          max_tokens=5, job_id="jb_0000000000000002", phase="live")
    assert len(httpserver.log) == 0


@pytest.mark.parametrize("status,exc,code", [
    (429, Retryable, "HTTP_429"), (503, Retryable, "HTTP_503"), (401, Fatal, "KEY_INVALID"),
    (402, Fatal, "CREDIT_LIMIT"), (404, Fatal, "NO_ZDR_ROUTE"), (400, Fatal, "HTTP_400"),
])
async def test_error_mapping(client, httpserver, status, exc, code):
    httpserver.expect_request("/api/v1/chat/completions").respond_with_data("x", status=status)
    with pytest.raises(exc) as e:
        await client.chat(model_key="haiku", messages=MSG, max_tokens=5, job_id="jb_0000000000000003", phase="live")
    assert e.value.code == code


async def test_403_with_limit_source_is_credit_limit(client, httpserver):
    httpserver.expect_request("/api/v1/chat/completions").respond_with_json(
        {"error": {"code": 403, "message": "Key limit exceeded", "metadata": {"limit_source": "openrouter_key_limit"}}},
        status=403)
    with pytest.raises(Fatal) as e:
        await client.chat(model_key="haiku", messages=MSG, max_tokens=5, job_id="jb_0000000000000007", phase="live")
    assert e.value.code == "CREDIT_LIMIT"


async def test_cap_blocks_before_sending(client, httpserver, settings):
    settings.set("cap_daily_usd", 0.0)
    with pytest.raises(CapHit):
        await client.chat(model_key="haiku", messages=MSG, max_tokens=5, job_id="jb_0000000000000004", phase="live")
    assert len(httpserver.log) == 0


async def test_offline_raises_network_down(client, httpserver):
    client.net.set_online(False, "app")
    with pytest.raises(NetworkDown):
        await client.chat(model_key="haiku", messages=MSG, max_tokens=5, job_id="jb_0000000000000005", phase="live")


async def test_connect_error_flips_offline(con, settings, clock, pinned):
    def boom(request):
        raise httpx.ConnectError("no route")
    net = NetState()
    c = OpenRouterClient(pinned=pinned, settings=settings, spend=SpendMeter(con, clock, settings), net=net,
                         key_provider=lambda: "k", clock=clock, base_url="http://127.0.0.1:9/api/v1",
                         http=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    with pytest.raises(NetworkDown):
        await c.chat(model_key="haiku", messages=MSG, max_tokens=5, job_id="jb_0000000000000008", phase="live")
    assert net.online is False


async def test_no_key_is_fatal(con, settings, clock, pinned, httpserver):
    c = OpenRouterClient(pinned=pinned, settings=settings, spend=SpendMeter(con, clock, settings), net=NetState(),
                         key_provider=lambda: None, clock=clock, base_url=httpserver.url_for("/api/v1"))
    with pytest.raises(Fatal) as e:
        await c.chat(model_key="haiku", messages=MSG, max_tokens=5, job_id="jb_0000000000000009", phase="live")
    assert e.value.code == "NO_KEY"


async def test_paid_lock_blocks_real_host(con, settings, clock, pinned):
    def boom(request):
        raise AssertionError("must not send")
    c = OpenRouterClient(pinned=pinned, settings=settings, spend=SpendMeter(con, clock, settings), net=NetState(),
                         key_provider=lambda: "k", clock=clock, base_url="https://openrouter.ai/api/v1",
                         http=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    with pytest.raises(Fatal) as e:
        await c.chat(model_key="haiku", messages=MSG, max_tokens=5, job_id="jb_0000000000000006", phase="live")
    assert e.value.code == "PAID_LOCKED"


async def test_systemone_goes_through_choke_point_with_zdr_and_cost(client, httpserver, con):
    from conftest import FIX
    resp = json.loads((FIX / "jev_response_example.json").read_text())
    httpserver.expect_request("/api/v1/systemone", method="POST").respond_with_json(resp)
    body = {"model": "typesafe/jev-1.13", "state": "short scrubbed text",
            "questions": {"worth": {"type": "noul", "instructions": "Worth a note?"}}}
    out = await client.systemone(body, job_id="jb_00000000000000b1", phase="build",
                                 build_id="bd_000000000001", est_in_tokens=100)
    sent = json.loads(httpserver.log[0][0].data)
    assert sent["provider"]["zdr"] is True and out["answers"]["is_bug"]["type"] == "noul"
    row = con.execute("select model, usd, build_id from spend").fetchone()
    assert row["model"] == "typesafe/jev-1.13" and row["usd"] == pytest.approx(1.9992e-05)
    assert row["build_id"] == "bd_000000000001"


async def test_probe_reports_reachability(client, httpserver, con, settings, clock, pinned):
    httpserver.expect_request("/", method="HEAD").respond_with_data("")
    assert await client.probe() is True
    dead = OpenRouterClient(pinned=pinned, settings=settings, spend=SpendMeter(con, clock, settings),
                            net=NetState(), key_provider=lambda: "k", clock=clock,
                            base_url="http://127.0.0.1:9/api/v1")
    assert await dead.probe() is False


async def test_user_approved_call_passes_the_lock_only_inside_its_scope(con, settings, clock, pinned, chat_ok):
    from misty_engine.openrouter import user_approved
    sent = []
    def ok(request):
        sent.append(request)
        return httpx.Response(200, json=chat_ok)
    c = OpenRouterClient(pinned=pinned, settings=settings, spend=SpendMeter(con, clock, settings), net=NetState(),
                         key_provider=lambda: "k", clock=clock, base_url="https://openrouter.ai/api/v1",
                         http=httpx.AsyncClient(transport=httpx.MockTransport(ok)))
    with user_approved():                         # Isaac clicked "Test saved key"
        await c.chat(model_key="haiku", messages=MSG, max_tokens=5, job_id=None, phase="live")
    assert len(sent) == 1 and settings.get("paid_calls_allowed") is not True
    with pytest.raises(Fatal) as e:               # outside the click, still locked
        await c.chat(model_key="haiku", messages=MSG, max_tokens=5, job_id=None, phase="live")
    assert e.value.code == "PAID_LOCKED" and len(sent) == 1


# ---- Q1: model keys, per-model reasoning body, purpose recorded

async def test_strong_sends_effort_not_enabled_and_has_token_headroom(client, httpserver, con, chat_ok):
    httpserver.expect_request("/api/v1/chat/completions", method="POST").respond_with_json(chat_ok)
    await client.chat(model_key="strong", messages=MSG, max_tokens=600, job_id=None, phase="build",
                      build_id="bd_000000000001", purpose="areas")
    body = json.loads(httpserver.log[0][0].data)
    assert body["model"] == "anthropic/claude-opus-5.5"
    assert "enabled" not in body["reasoning"] and body["reasoning"] == {"effort": "low"}
    assert body["max_tokens"] >= 600 + 2000
    assert body["provider"]["order"] == ["google-vertex/global", "amazon-bedrock"]
    assert con.execute("SELECT purpose FROM spend").fetchone()[0] == "areas"


@pytest.mark.parametrize("key,model,order", [
    ("haiku", "anthropic/claude-haiku-5.5", ["google-vertex/global", "amazon-bedrock"]),
    ("cheap", "qwen/qwen3-30b-a3b-instruct-2507", ["nebius/fp8", "siliconflow/fp8"]),
])
async def test_haiku_and_cheap_keep_reasoning_disabled(client, httpserver, chat_ok, key, model, order):
    httpserver.expect_request("/api/v1/chat/completions", method="POST").respond_with_json(chat_ok)
    await client.chat(model_key=key, messages=MSG, max_tokens=50, job_id=None, phase="live")
    body = json.loads(httpserver.log[0][0].data)
    assert body["model"] == model and body["reasoning"] == {"enabled": False}
    assert body["max_tokens"] == 50 and body["provider"]["order"] == order
    assert body["provider"]["zdr"] is True


def test_pinned_strong_reasoning_has_no_enabled_key(pinned):
    assert "enabled" not in pinned.models.strong_reasoning
    assert pinned.models.haiku_reasoning == {"enabled": False}


async def test_purpose_subcap_raises_caphit_with_reason(client, httpserver, settings, clock, con):
    client.spend.record(provider="openrouter", model="x", job_id=None, build_id=None, usd=0.0099, purpose="judge")
    with pytest.raises(CapHit) as e:
        await client.chat(model_key="haiku", messages=MSG, max_tokens=500, job_id=None, phase="live",
                          purpose="judge")
    assert e.value.code == "CAP_PURPOSE:judge" and len(httpserver.log) == 0


async def test_a_no_zdr_call_is_refused_unless_the_dev_setting_allows_it(client):
    import pytest
    from misty_engine.errors import PrivacyViolation
    client.settings.set("allow_no_zdr", False)
    with pytest.raises(PrivacyViolation):
        await client.decide({"model": "perplexity/pplx-decider-v1.3:free", "state": {}, "questions": {}}, purpose="t")
