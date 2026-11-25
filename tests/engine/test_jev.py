import json

import pytest

from misty_engine.errors import Fatal, PrivacyViolation
from misty_engine.jev import ROUTE, TRIAGE, WORTH, JevClient, trim_to_tokens
from misty_engine.jev_wire import Answer, Question, from_response, to_request
from misty_engine.types import approx_tokens

from conftest import FIX


def _fixture_questions():
    return [Question("is_bug", "Is the customer reporting a software defect?", ("yes", "no")),
            Question("team", "Which team should own this ticket?", ("account", "frontend", "payments"))]


def test_wire_request_has_pinned_shape():
    req = to_request("typesafe/jev-1.13", "ctx", _fixture_questions())
    fix = json.loads((FIX / "jev_request_example.json").read_text())
    assert set(req) == {"model", "state", "questions"} and req["model"] == fix["model"]
    assert req["questions"]["is_bug"]["type"] == "noul" == fix["questions"]["is_bug"]["type"]
    assert req["questions"]["team"]["type"] == "choice"
    assert set(req["questions"]["team"]["criteria"]) == {"account", "frontend", "payments"}


def test_wire_response_maps_to_answers():
    ans = from_response(json.loads((FIX / "jev_response_example.json").read_text()), _fixture_questions())
    assert ans["is_bug"] == Answer(("yes",), 0.96)
    assert ans["team"].choice == ("payments",) and ans["team"].confidence == 0.75
    assert all(0.0 <= a.confidence <= 1.0 for a in ans.values())


def test_noul_below_half_is_no_with_confidence_of_the_other_side():
    a = from_response({"answers": {"worth": {"type": "noul", "noul": 0.2}}}, WORTH)["worth"]
    assert a.choice == ("no",) and a.confidence == pytest.approx(0.8)


def test_from_response_rejects_unknown_choice_and_missing_question():
    bad = {"answers": {"team": {"type": "choice", "choice": "banana", "confidence": 0.9}}}
    with pytest.raises(Fatal) as e:
        from_response(bad, [Question("team", "t", ("account", "frontend"))])
    assert e.value.code == "JEV_BAD_RESPONSE"
    with pytest.raises(Fatal):
        from_response({"answers": {}}, WORTH)
    with pytest.raises(Fatal):
        from_response({}, WORTH)


def test_multi_select_is_one_noul_per_option():
    q = ROUTE[1]
    req = to_request("m", "x", [q])
    assert len(req["questions"]) == 7 and all(v["type"] == "noul" for v in req["questions"].values())
    answers = {f"sources__{c}": {"type": "noul", "noul": 0.9 if c in ("files", "mail") else 0.1} for c in q.choices}
    a = from_response({"answers": answers}, [q])["sources"]
    assert a.choice == ("files", "mail")


def test_trim_keeps_whole_lines():
    text = "\n".join(f"line {i} " + "word " * 20 for i in range(400))
    out = trim_to_tokens(text, 300)
    assert approx_tokens(out) <= 300 and out.startswith("line 0") and all(l.startswith("line ") for l in out.split("\n"))
    assert trim_to_tokens("short", 300) == "short"


async def test_context_trimmed_to_1500_tokens_plus_card(fctx, fake_or):
    jev = JevClient(fctx.openrouter, fctx.pinned, lambda: "CARD " * 20)
    big = "\n".join("sentence number %d about the thing" % i for i in range(2000))
    await jev.ask(big, WORTH, job_id="jb_00000000000000b0", phase="build", build_id="bd_000000000001")
    sent = fake_or.last_jev_state
    assert approx_tokens(sent) <= 1500 + approx_tokens("CARD " * 20) + 5 and sent.startswith("CARD")


async def test_jev_call_has_zdr_and_goes_through_client(fctx, fake_or):
    a = await fctx.jev.worth("some scrubbed text", job_id="jb_00000000000000b1", phase="build",
                             build_id="bd_000000000001")
    assert a.choice == ("yes",)
    assert fake_or.server.log[0][0].path == "/api/v1/systemone"
    assert json.loads(fake_or.server.log[0][0].data)["provider"]["zdr"] is True
    assert fctx.con.execute("select count(*) from spend").fetchone()[0] == 1


async def test_jev_unscrubbed_excerpt_is_blocked(fctx, fake_or):
    with pytest.raises(PrivacyViolation):
        await fctx.jev.worth("password: hunter2", job_id="jb_00000000000000b2", phase="build",
                             build_id="bd_000000000001")
    assert fake_or.request_count == 0


async def test_triage_questions_and_overrides(fctx, fake_or):
    fake_or.set_answers({"matters": ("yes", 0.9), "action": ("reply", 0.8), "area": ("school", 0.7),
                         "urgency": ("now", 0.95)})
    ans = await fctx.jev.triage("hello", ["school", "igem"], job_id="jb_00000000000000b3", phase="live")
    assert ans["action"].choice == ("reply",) and ans["area"].choice == ("school",)
    assert [q.id for q in TRIAGE(["a"])] == ["matters", "action", "area", "urgency"]


async def test_route_selects_sources(fctx, fake_or):
    fake_or.set_answers({"route": ("file", 0.9), "sources": (("files", "mail"), 0.9), "when": ("month", 0.8)})
    ans = await fctx.jev.route("physics IA from last month", job_id="jb_00000000000000b4", phase="live")
    assert ans["route"].choice == ("file",) and ans["sources"].choice == ("files", "mail")
    assert ans["when"].choice == ("month",)
