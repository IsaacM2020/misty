"""E9: "teach me this". Which path (code, 30 phrasings), the source, the map, the part, the lesson. Fake model, no paid call."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from misty_engine import chat, frame, gate
from misty_engine.teach import build as tb
from misty_engine.teach import live, match, plan
from misty_engine.teach import map as tm
from misty_engine.teach import sources as ts
from misty_engine.teach import this
from test_chat import ScriptBrain, nothing, no
from test_teach_live import make_plan

S1 = "Pacing a long run means starting slower than race pace so that the last miles feel strong."
S2 = "Negative splits are a sign of good control because the second half is faster than the first."
S3 = "Fuel for runs over ninety minutes should be taken every forty minutes to keep energy steady."
TEXT = f"{S1}\n\n{S2}\n\n{S3}"
TOPICS = [("pacing", "Pacing a long run", S1), ("negative splits", "Negative splits", S2), ("fuel", "Fuelling long runs", S3)]


def run(coro):
    return asyncio.run(coro)


# ---- which path: 15 each way (bar 28 of 30), pure code -------------------------------------------------------------
CH, WS, URL = "/Users/sam/chapter3.pdf", "/Users/sam/worksheet2.pdf", "https://x.test/article"
TEACH = [
    ("teach me this", CH), ("can you teach me this chapter", CH), ("go through this with me", CH), ("walk me through this page", CH),
    ("teach me what's on this page", CH), ("take me through this", CH), ("go over this with me", CH), ("teach us this", CH),
    ("teach me this section", CH), ("I want you to teach me this", CH), ("please walk me through this", CH),
    ("teach me this article", URL), ("go through this page with me", URL),
    ("teach me what we did in chem today", ""), ("walk me through what we covered in class today", ""),
]
SOLVE = [
    ("help me with this", ""), ("can you help me with this question", ""), ("I'm stuck on question 3 part b", ""), ("I don't get this", ""),
    ("help me solve this problem", ""), ("work this out", ""), ("explain this to me", ""), ("how do I do this", ""),
    ("I'm confused on this", ""), ("solve this", ""), ("teach me this", ""),
    ("help me with question 4", WS), ("I'm stuck on q 2", CH), ("help me with this problem", "/Users/sam/problems.pdf"),
    ("how do I do number 3", CH),
]


def test_the_thirty_phrasings():
    ok = sum(this.which_path(t, d) == "teach" for t, d in TEACH) + sum(this.which_path(t, d) == "solve" for t, d in SOLVE)
    assert ok >= 28, [(t, d) for t, d in TEACH if this.which_path(t, d) != "teach"] + [(t, d) for t, d in SOLVE if this.which_path(t, d) != "solve"]


def test_all_thirty_exactly():
    assert all(this.which_path(t, d) == "teach" for t, d in TEACH)
    assert all(this.which_path(t, d) == "solve" for t, d in SOLVE)


def test_unclear_asks_and_other_talk_is_none():
    assert this.which_path("help me with this", CH) == "ask"
    assert this.which_path("what's the weather like", CH) == "none"
    assert this.which_path("open my mail", "") == "none"
    assert this.which_path("teach me about moles", CH) == "none"            # a named topic is the model's teach start, not "this"


# ---- fixtures: three sources ---------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def fake_plans(monkeypatch):
    made = []

    async def fake(ctx, topic_id, **kw):
        src = ctx.con.execute("SELECT source_id FROM teach_topics WHERE id=?", (topic_id,)).fetchone()[0]
        from test_teach_live import put_plan
        put_plan(ctx, topic_id, src)
        made.append(topic_id)
        return {"status": "usable", "why": ""}
    monkeypatch.setattr(plan, "make", fake)
    return made


def set_map_reply(fake_or, no, topics=TOPICS):
    fake_or.chat_returns(json.dumps({"topics": [{"key": k, "title": t, "chunk_no": no, "quote": q} for k, t, q in topics], "needs": []}))


@pytest.fixture
def pdf(fctx, tmp_path, fake_or):
    p = tmp_path / "chapter3.txt"
    p.write_text(TEXT)
    fctx.open_doc = str(p)
    return str(p)


def topic_title(ctx):
    r = live.current(ctx)
    return ctx.con.execute("SELECT title FROM teach_topics WHERE id=?", (r["topic_id"],)).fetchone()[0]


def test_a_file_maps_inline_and_starts_a_lesson_on_the_named_part(fctx, fake_or, pdf):
    set_map_reply(fake_or, ts.chunks(fctx, run(ts.add(fctx, pdf))["id"])[0][0])
    r = run(this.start_this(fctx, "this", "the part about negative splits"))
    assert r["ok"] and r["mapped_now"] and r["picked"] == "Negative splits"
    assert topic_title(fctx) == "Negative splits"
    assert "next_topic" in r and r["next_topic"]
    assert fctx.con.execute("select count(*) from teach_sources").fetchone()[0] == 1


def test_a_web_article_starts_on_the_right_topic(fctx, fake_or, monkeypatch):
    async def page(url):
        return TEXT
    monkeypatch.setattr(ts.web, "read_full", page)
    fctx.open_doc = "https://x.test/running-guide"
    sid = run(ts.add(fctx, "https://x.test/running-guide"))["id"]
    set_map_reply(fake_or, ts.chunks(fctx, sid)[0][0])
    r = run(this.start_this(fctx, "this", "the fuel part"))
    assert r["ok"] and topic_title(fctx) == "Fuelling long runs"


def test_a_class_starts_on_the_right_topic_and_links_its_subject(fctx, fake_or):
    fctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status) VALUES ('cs_aaaa1111','Running','Run class',?,'noted')",
                     (fctx.clock.now() - 3600,))
    ids = [fctx.con.execute("INSERT INTO class_segments(session_id,t_start,t_end,source,text) VALUES ('cs_aaaa1111',?,?,'room',?)",
                            (i * 10, i * 10 + 9, t)).lastrowid for i, t in enumerate((S1, S2, S3))]
    fake_or.chat_returns(json.dumps({"topics": [{"key": k, "title": t, "chunk_no": ids[i], "quote": q} for i, (k, t, q) in enumerate(TOPICS)], "needs": []}))
    r = run(this.start_this(fctx, "class running", "negative splits"))
    assert r["ok"] and topic_title(fctx) == "Negative splits"
    sub = fctx.con.execute("select * from teach_subjects").fetchone()
    assert sub["class_subject"] == "Running"                 # E4's link: his notes and this lesson feed the same topics


def test_no_words_picks_the_first_ready_topic_and_says_which_and_the_next(fctx, fake_or, pdf):
    set_map_reply(fake_or, ts.chunks(fctx, run(ts.add(fctx, pdf))["id"])[0][0])
    r = run(this.start_this(fctx, "this", ""))
    assert r["picked"] == "Pacing a long run" and r["next_topic"] == "Negative splits" and r["said_which"]


def test_the_part_the_frame_points_at_is_used_when_he_says_nothing(fctx, fake_or, pdf):
    set_map_reply(fake_or, ts.chunks(fctx, run(ts.add(fctx, pdf))["id"])[0][0])
    fctx.open_near = "ninety minutes should be taken every forty"
    r = run(this.start_this(fctx, "this", ""))
    assert r["picked"] == "Fuelling long runs"


def test_a_second_teach_me_this_adds_no_source_and_no_second_map(fctx, fake_or, pdf):
    set_map_reply(fake_or, ts.chunks(fctx, run(ts.add(fctx, pdf))["id"])[0][0])
    run(this.start_this(fctx, "this", "pacing"))
    calls = len(fake_or.chat_bodies)
    r = run(this.start_this(fctx, "this", "fuel"))
    assert r["ok"] and not r["mapped_now"] and len(fake_or.chat_bodies) == calls
    assert fctx.con.execute("select count(*) from teach_sources").fetchone()[0] == 1
    assert fctx.con.execute("select count(*) from teach_maps").fetchone()[0] == 1
    assert topic_title(fctx) == "Fuelling long runs"


def test_a_part_with_no_hit_falls_back_to_the_topic_matcher(fctx, fake_or, pdf, monkeypatch):
    set_map_reply(fake_or, ts.chunks(fctx, run(ts.add(fctx, pdf))["id"])[0][0])
    seen = []

    async def pick(ctx, subject_id, text):
        seen.append(text)
        return ctx.con.execute("select id from teach_topics where title='Negative splits'").fetchone()[0]
    monkeypatch.setattr(match, "topic_for", pick)
    r = run(this.start_this(fctx, "this", "the bit about hydration salts"))
    assert seen and r["picked"] == "Negative splits"


# ---- big sources, refusals ----------------------------------------------------------------------------------------
def test_a_big_source_queues_the_map_and_the_job_tells_him_through_the_gate(fctx, fake_or, tmp_path, monkeypatch):
    p = tmp_path / "big.txt"
    p.write_text((TEXT + "\n\n") * 120)                           # over 12k characters
    fctx.open_doc = str(p)
    r = run(this.start_this(fctx, "this", ""))
    assert r["ok"] and r["queued"] and not live.current(fctx) and "big" in r["say"]
    job = fctx.con.execute("select * from jobs where kind='teach_map'").fetchone()
    assert job is not None
    sent = []

    async def submit(ctx, m):
        sent.append(m)
    monkeypatch.setattr(gate, "submit", submit)
    sid = fctx.con.execute("select id from teach_sources").fetchone()[0]
    set_map_reply(fake_or, ts.chunks(fctx, sid)[0][0])
    payload = json.loads(job["payload"]) if isinstance(job["payload"], str) else job["payload"]
    run(tb.teach_map_job(SimpleNamespace(payload=payload, id="j1"), fctx))
    assert len(sent) == 1 and sent[0].own and "ready" in sent[0].text and "Start?" in sent[0].text


def test_nothing_open_and_private_are_refused_with_the_reason(fctx):
    fctx.open_doc = ""
    r = run(this.start_this(fctx, "this", ""))
    assert r["ok"] is False and "open" in r["error"]


def test_a_secret_file_is_refused(fctx, tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=b")
    fctx.open_doc = str(p)
    r = run(this.start_this(fctx, "this", ""))
    assert r["ok"] is False and "secret" in r["error"]


def test_a_failed_map_says_why_and_starts_nothing(fctx, fake_or, pdf):
    fake_or.chat_returns("not json at all")
    r = run(this.start_this(fctx, "this", ""))
    assert r["ok"] is False and not live.current(fctx)


# ---- the tool, and chat ---------------------------------------------------------------------------------------------
def test_the_teach_tool_takes_what_and_part(fctx, fake_or, pdf):
    t = next(t for t in chat.TOOLS if t["function"]["name"] == "teach")["function"]["parameters"]["properties"]
    assert "what" in t and "part" in t
    set_map_reply(fake_or, ts.chunks(fctx, run(ts.add(fctx, pdf))["id"])[0][0])
    statuses = []

    async def st(x):
        statuses.append(x)

    async def cf(*a, **k):
        return True
    r = run(chat.run_tool(fctx, "teach", {"action": "start", "what": "this", "part": "pacing"}, on_status=st, confirm=cf))
    assert r["ok"] and r["picked"] == "Pacing a long run" and any("mapping" in s.lower() for s in statuses)


def test_text_she_read_cannot_start_it(fctx, pdf):
    async def st(x): ...
    async def cf(*a, **k): return True
    r = run(chat.run_tool(fctx, "teach", {"action": "start", "what": "this"}, on_status=st, confirm=cf, source="text"))
    assert r["ok"] is False


@pytest.mark.asyncio
async def test_teach_me_this_with_a_document_skips_the_old_look_and_solve(fctx):
    brain = ScriptBrain({"content": "ok"})
    fctx.openrouter.post = brain.post
    f = frame.parse({"app": "Preview", "title": "Chapter 3", "doc": "/Users/sam/chapter3.pdf"})
    out = await chat.send(fctx, "teach me this", on_delta=nothing, on_status=nothing, confirm=no, frame=f)
    assert not out["actions"]


@pytest.mark.asyncio
async def test_help_me_with_this_and_a_chapter_open_does_not_solve_it_asks(fctx):
    brain = ScriptBrain({"content": "Teach the whole page, or help with one question?"})
    fctx.openrouter.post = brain.post
    f = frame.parse({"app": "Preview", "title": "Chapter 3", "doc": "/Users/sam/chapter3.pdf"})
    out = await chat.send(fctx, "help me with this", on_delta=nothing, on_status=nothing, confirm=no, frame=f)
    assert not out["actions"]
    sys = brain.bodies[0]["messages"][0]["content"]
    sys = "".join(x["text"] for x in sys) if isinstance(sys, list) else sys
    assert "one question" in sys


def test_the_old_path_stays_when_nothing_is_open_or_a_question_is_named():
    from misty_engine import tutor
    assert tutor.wants_help("help me with this") and this.which_path("help me with this", "") == "solve"
    assert this.which_path("I'm stuck on question 2", "/x/chapter.pdf") == "solve"


def test_the_rule_names_what_and_the_next_topic():
    assert "what" in chat.TEACH_START_RULE and "next" in chat.TEACH_START_RULE.lower()
