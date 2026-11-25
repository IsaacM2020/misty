import json

import pytest

from misty_engine.errors import NetworkDown
from misty_engine.wiki.ask import NOT_IN_NOTES, ask_wiki, query
from test_context_pack import put


@pytest.fixture
def ctx_wiki(fctx):
    put(fctx, "projects/igem", "project", "iGEM wiki", "The iGEM wiki documents the team project.", importance=4,
        links=["areas/school"], now=["write part 3 docs"], timeline=["2026-10-16: wiki sync"])
    put(fctx, "areas/school", "area", "School", "School things and deadlines.", importance=4)
    return fctx


async def test_answer_filed_only_with_two_valid_citations(ctx_wiki, fake_or):
    fake_or.chat_text("iGEM is in design phase [[projects/igem]] with deadline Friday [[areas/school]].")
    r = await ask_wiki(ctx_wiki, "what's going on with iGEM", job_id="jb_00000000000000c1")
    assert r.filed == "answers/what-s-going-on-with-igem.md" and ctx_wiki.wiki.exists(r.filed)
    assert ctx_wiki.wiki.read(r.filed).fm["cites"] == ["projects/igem", "areas/school"]
    assert "[[answers/what-s-going-on-with-igem]]" in (ctx_wiki.paths.wiki / "index.md").read_text()
    fake_or.chat_text("Maybe [[projects/igem]] and [[made/up]].")
    r2 = await ask_wiki(ctx_wiki, "something else", job_id="jb_00000000000000c2")
    assert r2.filed is None and r2.citations == ["projects/igem"]


async def test_not_in_notes_is_not_filed(ctx_wiki, fake_or):
    fake_or.chat_text(NOT_IN_NOTES)
    r = await ask_wiki(ctx_wiki, "what is my blood type", job_id="jb_00000000000000c3")
    assert r.filed is None and r.answer == NOT_IN_NOTES


async def test_same_question_updates_same_file(ctx_wiki, fake_or):
    fake_or.chat_text("A [[projects/igem]] and [[areas/school]].")
    a = await ask_wiki(ctx_wiki, "What's going on with iGEM?", job_id=None)
    b = await ask_wiki(ctx_wiki, "what's going on with igem", job_id=None)
    assert a.filed == b.filed


async def test_ask_prompt_contains_pack_and_hits_and_zdr(ctx_wiki, fake_or, seed):
    seed("it_ask0000000000001", "raw note about the iGEM wiki parts", title="raw.md")
    fake_or.chat_text(NOT_IN_NOTES)
    await ask_wiki(ctx_wiki, "iGEM wiki", job_id=None)
    body = fake_or.chat_bodies[0]
    user = fake_or.chat_user_texts()[0]
    assert "## about-isaac" in user and "[[projects/igem]]" in user and "raw.md" in user and "Question:" in user
    assert body["provider"]["zdr"] is True and "only the notes" in body["messages"][0]["content"].lower() \
        or "ONLY the notes" in body["messages"][0]["content"]


async def test_query_routes(ctx_wiki, fake_or, seed):
    seed("it_ph0000000000001", "my physics IA draft about pendulums", title="physics-ia.docx")
    fake_or.chat_text("iGEM [[projects/igem]] and [[areas/school]]")
    fake_or.set_answers({"route": ("ask_wiki", 0.9)})
    r = await query(ctx_wiki, "what's up with igem")
    assert r["route"] == "ask_wiki" and r["citations"] == ["projects/igem", "areas/school"]
    fake_or.set_answers({"route": ("file", 0.9), "sources": (("files",), 0.9)})
    r = await query(ctx_wiki, "my physics IA draft")
    assert r["route"] == "file" and r["results"] and r["results"][0]["title"] == "physics-ia.docx"
    fake_or.set_answers({"route": ("action", 0.9)})
    r = await query(ctx_wiki, "email Aarav about friday")
    assert r["route"] == "action" and r["context_pack"]["text"].startswith("## about-isaac")


async def test_low_confidence_route_defaults_to_file_search(ctx_wiki, fake_or):
    fake_or.set_answers({"route": ("action", 0.4)})
    assert (await query(ctx_wiki, "something"))["route"] == "file"


async def test_query_falls_back_to_search_when_jev_down(ctx_wiki, fake_or, seed):
    seed("it_ph0000000000002", "physics IA pendulum", title="physics-ia.docx")
    ctx_wiki.net.set_online(False, "test")
    r = await query(ctx_wiki, "physics IA")
    assert r["route"] == "file" and r["degraded"] is True and r["results"]


async def test_ask_prompt_has_today_and_dated_lines_near_today(ctx_wiki, fake_or):
    # fake clock: 2026-10-15 in Singapore
    from test_context_pack import put
    put(ctx_wiki, "topics/weekly-schedule", "topic", "Weekly schedule", "Classes and blocks.",
        timeline=["2026-10-15: Tech Society meeting at 10:00", "2026-10-20: Chem test",
                  "2026-11-30: far away thing", "2026-09-01: old thing"])
    fake_or.chat_text(NOT_IN_NOTES)
    await ask_wiki(ctx_wiki, "what are my deliverables today", job_id=None)
    user = fake_or.chat_user_texts()[0]
    assert "Today is Thursday 2026-10-15" in user
    assert "Tech Society meeting" in user and "Chem test" in user
    assert "far away thing" not in user and "old thing" not in user
