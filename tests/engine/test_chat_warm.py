"""F1: warm the cache when he presses the key. `chat.warm` sends the same cached prefix a real turn would send (one shared
builder), with max_tokens 1 and no tools allowed, and it skips itself when a turn is recent, the day's warms are used, the
money is nearly gone, or she is in class. It never raises into the key press path."""
import asyncio
import itertools
import json

import pytest

from misty_engine import api_chat, chat, classes
from misty_engine.errors import Fatal
from misty_engine.logs import setup_logging


class WarmBrain:
    """Stands in for ctx.openrouter.post: records every body and its keyword arguments."""
    def __init__(self, cached=0, boom=None):
        self.bodies, self.kws, self.cached, self.boom = [], [], cached, boom

    async def post(self, path, body, *, on_delta=None, **kw):
        self.bodies.append(json.loads(json.dumps(body)))
        self.kws.append(kw)
        if self.boom:
            raise self.boom
        msg = {"role": "assistant", "content": ""}
        if on_delta:
            await on_delta("ok ")
            msg["content"] = "ok "
        return {"choices": [{"message": msg}],
                "usage": {"cost": 0.0009, "prompt_tokens": 11900, "prompt_tokens_details": {"cached_tokens": self.cached}}}


class CountingEmbedder:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def embed_query(self, text):
        self.calls.append(text)
        return self.inner.embed_query(text)

    def __getattr__(self, name):
        return getattr(self.inner, name)


async def nothing(_):
    return None


async def no(_):
    return False


async def real_turn(ctx, text="hello there", **kw):
    return await chat.send(ctx, text, on_delta=nothing, on_status=nothing, confirm=no, **kw)


def first_system_block(body):
    return body["messages"][0]["content"][0]


# ---- one builder, so the two bodies cannot drift ----------------------------------------------------------------------
HIDE = [set(s) for n in range(4) for s in itertools.combinations(["judge", "answer_ask", "lets_go"], n)]     # 8 tool states


@pytest.mark.asyncio
@pytest.mark.parametrize("hidden", HIDE, ids=lambda h: "hide-" + ("+".join(sorted(h)) or "none"))
@pytest.mark.parametrize("voice", [False, True])
async def test_warm_body_matches_the_real_body_byte_for_byte(ctx, clock, monkeypatch, hidden, voice):
    monkeypatch.setattr(chat, "_in_state", lambda c, name: name not in hidden)          # idle, ask open, tutoring, lets_go ready, ...
    real, warm = WarmBrain(), WarmBrain()
    ctx.openrouter = real
    await real_turn(ctx, "hello there", voice=voice)
    clock.advance(600)                                              # the real turn is no longer recent
    ctx.openrouter = warm
    out = await chat.warm(ctx, voice=voice)
    assert out["kind"] == "sent"
    r, w = real.bodies[0], warm.bodies[0]
    assert json.dumps(first_system_block(r)) == json.dumps(first_system_block(w))        # text and cache_control, byte for byte
    assert first_system_block(w)["cache_control"] == {"type": "ephemeral"}
    assert json.dumps(r["tools"]) == json.dumps(w["tools"])
    assert [r[k] for k in ("model", "provider", "reasoning", "temperature")] == [w[k] for k in ("model", "provider", "reasoning", "temperature")]


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", [False, True])
async def test_warm_sees_a_live_lesson_rule_the_same_way_a_real_turn_does(ctx, clock, monkeypatch, voice):
    monkeypatch.setattr(chat.teachlive, "rule_block", lambda c: "A lesson is running.")
    real, warm = WarmBrain(), WarmBrain()
    ctx.openrouter = real
    await real_turn(ctx, voice=voice)
    clock.advance(600)
    ctx.openrouter = warm
    await chat.warm(ctx, voice=voice)
    assert json.dumps(first_system_block(real.bodies[0])) == json.dumps(first_system_block(warm.bodies[0]))
    assert json.dumps(real.bodies[0]["tools"]) == json.dumps(warm.bodies[0]["tools"])


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", [False, True])
async def test_the_exam_prompt_comes_from_the_same_builder_and_is_unchanged(ctx, voice):
    real = WarmBrain()
    ctx.openrouter = real
    await real_turn(ctx, "what is on my calendar", voice=voice, exam=True)
    system, history, tools, _ = chat.prefix(ctx, "what is on my calendar", voice=voice, exam=True)
    assert json.dumps(real.bodies[0]["messages"][0]) == json.dumps(system)
    assert json.dumps(real.bodies[0]["tools"]) == json.dumps(tools)
    assert {t["function"]["name"] for t in tools} == chat.EXAM_TOOLS and history == []


def test_messages_is_the_builder_plus_the_user_line(ctx):
    system, history, tools, tail = chat.prefix(ctx, "hi there", voice=True)
    assert chat.messages(ctx, "hi there", voice=True) == [system] + history + [chat.user_message("hi there", tail, mark=False)]
    assert json.dumps(tools) == json.dumps(chat.tools_for(ctx))


# ---- the body ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_warm_asks_for_one_token_and_no_tools_and_counts_as_chat_spend(ctx):
    b = WarmBrain(cached=9000)
    ctx.openrouter = b
    out = await chat.warm(ctx)
    body, kw = b.bodies[0], b.kws[0]
    assert body["max_tokens"] == 1 and body["tool_choice"] == "none" and body["tools"]
    assert body["messages"][-1]["role"] == "user"
    assert kw["purpose"] == "chat" and kw["phase"] == "live" and kw["estimate_usd"] > 0
    assert out == {"kind": "sent", "cached": 9000, "ms": out["ms"], "usd": 0.0009} and out["ms"] >= 0


@pytest.mark.asyncio
async def test_warm_is_allowed_to_spend_before_paid_calls_are_unlocked(ctx):
    seen = []

    class B(WarmBrain):
        async def post(self, *a, **kw):
            from misty_engine import openrouter
            seen.append(openrouter._USER_APPROVED.get())
            return await super().post(*a, **kw)
    ctx.openrouter = B()
    await chat.warm(ctx)
    assert seen == [True]                      # the key press is his own act, like a typed message


# ---- skip rules -------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_real_turn_in_the_last_four_minutes_means_no_warm(ctx, clock):
    b = WarmBrain()
    ctx.openrouter = b
    chat._save(ctx, "user", "hello")
    clock.advance(239)
    assert await chat.warm(ctx) == {"kind": "skipped", "why": "recent"}
    assert b.bodies == []
    clock.advance(2)
    assert (await chat.warm(ctx))["kind"] == "sent"


@pytest.mark.asyncio
async def test_a_second_warm_inside_four_minutes_is_skipped_and_one_after_is_not(ctx, clock):
    b = WarmBrain()
    ctx.openrouter = b
    assert (await chat.warm(ctx))["kind"] == "sent"
    clock.advance(200)
    assert await chat.warm(ctx) == {"kind": "skipped", "why": "recent"}
    clock.advance(60)
    assert (await chat.warm(ctx))["kind"] == "sent"
    assert len(b.bodies) == 2


@pytest.mark.asyncio
async def test_the_daily_limit_of_warms_stops_them_and_a_new_day_resets_it(ctx, clock, monkeypatch):
    monkeypatch.setattr(chat, "WARM_MAX_PER_DAY", 3)
    b = WarmBrain()
    ctx.openrouter = b
    for _ in range(3):
        assert (await chat.warm(ctx))["kind"] == "sent"
        clock.advance(300)
    assert await chat.warm(ctx) == {"kind": "skipped", "why": "cap"}
    clock.advance(86400)
    assert (await chat.warm(ctx))["kind"] == "sent"


def test_the_constants_are_the_cards():
    assert chat.WARM_MAX_PER_DAY == 20 and chat.WARM_QUIET_S == 240


@pytest.mark.asyncio
async def test_a_nearly_spent_day_skips_the_warm(ctx):
    cap = float(ctx.settings.get("cap_daily_usd"))
    ctx.spend.record(provider="openrouter", model="m", job_id=None, build_id=None, usd=cap * 0.85, purpose="chat")
    b = WarmBrain()
    ctx.openrouter = b
    assert await chat.warm(ctx) == {"kind": "skipped", "why": "cap"}
    assert b.bodies == []


@pytest.mark.asyncio
async def test_in_class_there_is_no_warm_and_no_embedder_wake(ctx):
    classes.start(ctx.con, ctx.clock, "Physics", None, None)
    ctx.embedder = CountingEmbedder(ctx.embedder)
    b = WarmBrain()
    ctx.openrouter = b
    assert await chat.warm(ctx) == {"kind": "skipped", "why": "class"}
    assert b.bodies == [] and ctx.embedder.calls == []


@pytest.mark.asyncio
async def test_an_ended_class_does_not_block_it(ctx):
    s = classes.start(ctx.con, ctx.clock, "Physics", None, None)
    ctx.con.execute("UPDATE class_sessions SET status='ended' WHERE id=?", (s["id"],))
    ctx.openrouter = WarmBrain()
    assert (await chat.warm(ctx))["kind"] == "sent"


# ---- it never raises into the key press -------------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("boom", [Fatal("PAID_LOCKED"), RuntimeError("x"), asyncio.TimeoutError()])
async def test_a_failing_call_is_a_skip_not_an_error(ctx, boom):
    ctx.openrouter = WarmBrain(boom=boom)
    out = await chat.warm(ctx)
    assert out == {"kind": "skipped", "why": "error"}


@pytest.mark.asyncio
async def test_a_failed_warm_does_not_use_up_the_four_minutes_or_the_day(ctx, clock):
    ctx.openrouter = WarmBrain(boom=RuntimeError("x"))
    await chat.warm(ctx)
    ctx.openrouter = b = WarmBrain()
    assert (await chat.warm(ctx))["kind"] == "sent" and len(b.bodies) == 1


@pytest.mark.asyncio
async def test_a_broken_builder_is_a_skip_too(ctx, monkeypatch):
    def bad(*a, **k):
        raise ValueError("boom")
    monkeypatch.setattr(chat, "prefix", bad)
    assert await chat.warm(ctx) == {"kind": "skipped", "why": "error"}


# ---- the embedder is woken on the same key press (part 1b) -------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_embedder_is_woken_even_when_the_model_warm_is_skipped(ctx):
    ctx.embedder = CountingEmbedder(ctx.embedder)
    ctx.openrouter = WarmBrain()
    chat._save(ctx, "user", "hello")
    assert (await chat.warm(ctx))["why"] == "recent"
    assert ctx.embedder.calls == ["warm"]


@pytest.mark.asyncio
async def test_a_failing_embedder_does_not_break_the_warm(ctx):
    class Bad:
        def embed_query(self, t):
            raise RuntimeError("no model")
    ctx.embedder = Bad()
    ctx.openrouter = WarmBrain()
    assert (await chat.warm(ctx))["kind"] == "sent"


@pytest.mark.asyncio
async def test_no_embedder_is_fine(ctx):
    ctx.embedder = None
    ctx.openrouter = WarmBrain()
    assert (await chat.warm(ctx))["kind"] == "sent"


# ---- the log line -----------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_warm_line_has_kind_why_cached_and_ms_and_nothing_else(ctx, misty_home):
    setup_logging(misty_home.logs)
    ctx.openrouter = WarmBrain(cached=8000)
    await chat.warm(ctx)
    await chat.warm(ctx)                                          # the second is skipped (recent)
    import logging
    for h in logging.getLogger("misty").handlers:
        h.flush()
    text = "".join(p.read_text() for p in misty_home.logs.glob("engine.log*"))
    lines = [ln for ln in text.splitlines() if "WARM" in ln]
    assert len(lines) == 2
    assert "kind=sent" in lines[0] and "cached=8000" in lines[0] and "ms=" in lines[0]
    assert "kind=skipped" in lines[1] and "why=recent" in lines[1]


# ---- the RPC ----------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chat_warm_is_an_rpc_that_does_not_wait_for_the_busy_lock(ctx):
    ctx.openrouter = WarmBrain()
    m = api_chat.chat_methods(ctx)
    assert "chat.warm" in m
    out = await asyncio.wait_for(m["chat.warm"]({}), 2)
    assert out["kind"] == "sent"


@pytest.mark.asyncio
async def test_the_rpc_rejects_a_bad_voice_flag(ctx):
    m = api_chat.chat_methods(ctx)
    with pytest.raises(Fatal):
        await m["chat.warm"]({"voice": "yes"})


@pytest.mark.asyncio
async def test_the_rpc_passes_voice_through(ctx):
    b = WarmBrain()
    ctx.openrouter = b
    m = api_chat.chat_methods(ctx)
    await m["chat.warm"]({"voice": False})
    ctx.clock.advance(300)
    await m["chat.warm"]({})
    plain, spoken = (first_system_block(x)["text"] for x in b.bodies)
    assert plain != spoken and len(spoken) > len(plain)           # the voice rules are in the spoken one only


# ---- the Swift side is one fire-and-forget call, first thing on the key --------------------------------------------------
def test_the_key_path_calls_warm_cache_before_anything_that_can_wait():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "app/Misty/Core/Voice/VoiceVM.swift").read_text()
    body = src.split("private func startTalk(viaWake: Bool = false) async {")[1]
    assert body.index("warmCache()") < body.index("askMic")
    call = src.split("public func warmCache() {")[1].split("\n    }\n")[0]
    assert "guard !classMode" in call and "Task.detached" in call and "try? await api.call(\"chat.warm\"" in call
    assert "await" not in call.split("Task.detached")[0]                    # nothing waits for it
