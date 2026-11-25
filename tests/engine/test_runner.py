import json

import pytest

from misty_engine.errors import CapHit, PrivacyViolation, Retryable
from misty_engine.runner import ApiRunner, RunResult, WikiText


async def test_run_returns_json_and_records_purpose(client, fake_or, con):
    fake_or.chat_text('noise {"a": 1} tail')
    r = ApiRunner(client, lambda: "live")
    out = await r.run("sys", ["p"], "demo", purpose="judge")
    assert isinstance(out, RunResult) and out.json == {"a": 1}
    assert con.execute("SELECT purpose FROM spend").fetchone()[0] == "judge"


async def test_run_rejects_secret_shaped_input(client, fake_or):
    r = ApiRunner(client, lambda: "live")
    with pytest.raises(PrivacyViolation) as e:
        await r.run("sys", ["key sk-or-v1-" + "a" * 40], "demo", purpose="judge")
    assert "UNSCRUBBED" in str(e.value)
    assert fake_or.request_count == 0                      # nothing left the machine


async def test_run_rejects_secret_in_the_system_prompt_too(client, fake_or):
    with pytest.raises(PrivacyViolation):
        await ApiRunner(client, lambda: "live").run("password: hunter2", ["p"], "demo", purpose="judge")
    assert fake_or.request_count == 0


async def test_run_non_json_is_retryable(client, fake_or):
    fake_or.chat_text("no json here")
    with pytest.raises(Retryable):
        await ApiRunner(client, lambda: "live").run("s", ["p"], "demo", purpose="judge")


async def test_json_array_or_garbage_between_braces_is_not_a_result(client, fake_or):
    fake_or.chat_text("{not json at all}")
    with pytest.raises(Retryable):
        await ApiRunner(client, lambda: "live").run("s", ["p"], "demo", purpose="judge")


async def test_message_shape_and_model_key_reach_the_wire(client, fake_or):
    fake_or.chat_text('{"ok": true}')
    # areas is a build-phase job (one big strong-model call, about $0.2): it fits the $3 build cap, never the $0.20 day
    await ApiRunner(client, lambda: "build").run("  SYS  ", ["one", "two"], "demo", purpose="areas",
                                                 model_key="strong", max_tokens=700, build_id="bd_000000000001")
    body = fake_or.chat_bodies[0]
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "one\n\ntwo"}]
    assert body["model"] == "anthropic/claude-opus-5.5" and "enabled" not in body["reasoning"]
    assert body["provider"]["zdr"] is True


async def test_phase_getter_is_read_per_call(client, fake_or, con):
    fake_or.chat_text('{"ok": true}')
    phase = {"v": "build"}
    r = ApiRunner(client, lambda: phase["v"])
    await r.run("s", ["p"], "demo", purpose="summarise", build_id="bd_000000000001")
    phase["v"] = "live"
    await r.run("s", ["p"], "demo", purpose="summarise")
    rows = con.execute("SELECT build_id FROM spend ORDER BY id").fetchall()
    assert [x[0] for x in rows] == ["bd_000000000001", None]


async def test_sub_cap_defers_instead_of_failing(client, fake_or, con):
    client.spend.record(provider="openrouter", model="x", job_id=None, build_id=None, usd=0.0099, purpose="judge")
    with pytest.raises(CapHit) as e:
        await ApiRunner(client, lambda: "live").run("s", ["p"], "demo", purpose="judge", max_tokens=400)
    assert e.value.code == "CAP_PURPOSE:judge" and fake_or.request_count == 0


async def test_run_log_line_has_no_content(client, fake_or, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="misty")
    fake_or.chat_text('{"secret_name": "Pranav"}')
    await ApiRunner(client, lambda: "live").run("s", ["Pranav's mum"], "demo", purpose="ask")
    blob = " ".join(r.getMessage() + json.dumps(getattr(r, "misty_fields", {})) for r in caplog.records)
    assert "RUN" in blob and "Pranav" not in blob


def test_wikitext_is_importable_from_runner_and_is_a_str():
    assert WikiText("x") == "x"


async def test_build_phase_call_without_build_id_is_attributed_to_the_latest_build(client, fake_or, con):
    """Stage 5 jobs carry no build id. In the build phase their spend must still count against the build caps."""
    con.execute("INSERT INTO builds(id, started_at, phase, status) VALUES ('bd_aaaaaaaaaaaa', 1, 'done', 'done')")
    con.execute("INSERT INTO builds(id, started_at, phase, status) VALUES ('bd_bbbbbbbbbbbb', 2, 'done', 'done')")
    fake_or.chat_text('{"ok": true}')
    await ApiRunner(client, lambda: "build").run("s", ["p"], "demo", purpose="summarise")
    assert con.execute("SELECT build_id, purpose FROM spend").fetchone()[:] == ("bd_bbbbbbbbbbbb", "summarise")


async def test_live_call_never_gets_a_build_id(client, fake_or, con):
    con.execute("INSERT INTO builds(id, started_at, phase, status) VALUES ('bd_aaaaaaaaaaaa', 1, 'done', 'done')")
    fake_or.chat_text('{"ok": true}')
    await ApiRunner(client, lambda: "live").run("s", ["p"], "demo", purpose="ask")
    assert con.execute("SELECT build_id FROM spend").fetchone()[0] is None
