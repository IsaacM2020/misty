"""E3: a source becomes a map. Fake model replies only, no paid call. A made-up stranger's subject, nothing about one person."""
import asyncio
import json

import pytest

from misty_engine import chat, pipeline, setting
from misty_engine.openrouter import user_approved
from misty_engine.teach import ask
from misty_engine.teach import build as tb
from misty_engine.teach import map as tm
from misty_engine.teach import sources as ts
from misty_engine.types import Job

S1 = "Pacing a long run means starting slower than race pace so that the last miles feel strong."
S2 = "Negative splits are a sign of good control because the second half is faster than the first."
S3 = "Fuel for runs over ninety minutes should be taken every forty minutes to keep energy steady."
TEXT = f"{S1}\n\n{S2}\n\n{S3}\n\nRest days let the body rebuild after hard weeks of training and prevent injury over time."


def run(coro):
    return asyncio.run(coro)


def reply(topics=(), needs=()):
    return json.dumps({"topics": [{"key": k, "title": t, "chunk_no": n, "quote": q} for k, t, n, q in topics],
                       "needs": [{"topic": a, "needs": b} for a, b in needs]})


@pytest.fixture
def src(fctx, tmp_path):
    p = tmp_path / "plan.txt"
    p.write_text(TEXT)
    r = run(ts.add(fctx, str(p)))
    return r["id"], ts.chunks(fctx, r["id"])[0][0]


@pytest.fixture
def subj(fctx):
    return tm.add_subject(fctx, "running", "Running")["id"]


def go(ctx, sid, subj, **kw):
    with user_approved():
        return run(tb.build_map(ctx, sid, subj, **kw))


GOOD = ("pacing", "Pacing a long run", S1)
NEG = ("negative splits", "Negative splits", S2)
FUEL = ("fuel", "Fuelling long runs", S3)


def topic_reply(no, *items, needs=()):
    return reply([(k, t, no, q) for k, t, q in items], needs)


# ---- windows -------------------------------------------------------------------------------------------------------
def test_windows_overlap_and_keep_the_ids():
    chunks = [(100 + i, "x" * 900) for i in range(80)]            # 72,000 characters
    w = tb.windows(chunks)
    assert len(w) >= 3
    assert all(sum(len(t) for _, t in x) <= tb.WINDOW_CHARS for x in w)
    assert w[0][-1] in w[1]                                       # the overlap is whole chunks
    assert [no for no, _ in chunks] == sorted({no for x in w for no, _ in x})   # nothing is left out
    assert tb.render([(553, "hello there")]) == "[553] hello there"


def test_one_short_source_is_one_window():
    assert len(tb.windows([(1, "a"), (2, "b")])) == 1
    assert tb.windows([]) == []


def test_a_single_huge_chunk_still_makes_progress():
    assert len(tb.windows([(1, "y" * 50_000), (2, "z" * 50_000)])) == 2


# ---- the model door ------------------------------------------------------------------------------------------------
def test_think_posts_with_reasoning_and_purpose_teach(fctx, fake_or):
    fake_or.chat_returns('{"ok": 1}')
    with user_approved():
        text, usd = run(ask.think(fctx, "sys", "user"))
    assert ask.parse_json(text) == {"ok": 1} and usd > 0
    b = fake_or.chat_bodies[0]
    assert b["reasoning"]["enabled"] is True and b["temperature"] == 0.1
    assert fctx.con.execute("select count(*) from spend where purpose='teach'").fetchone()[0] == 1


def test_think_without_thinking_does_not_enable_it(fctx, fake_or):
    fake_or.chat_returns("{}")
    with user_approved():
        run(ask.think(fctx, "s", "u", thinking=False))
    assert fake_or.chat_bodies[0]["reasoning"].get("enabled") is not True


def test_parse_json_refuses_text_with_no_object():
    with pytest.raises(ValueError):
        ask.parse_json("sorry")


# ---- build ---------------------------------------------------------------------------------------------------------
def test_good_topics_are_added_with_their_cite(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD, NEG))
    r = go(fctx, sid, subj, merge=False)
    assert r["status"] == "done" and r["topics"] == 2 and r["dropped"] == 0 and r["usd"] > 0
    t = fctx.con.execute("select * from teach_topics where subject_id=? and key='pacing'", (subj,)).fetchone()
    assert (t["source_id"], t["chunk_no"], t["quote"]) == (sid, no, S1)


def test_a_bad_quote_is_dropped_and_counted(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD, ("made up", "Made up", "Hydration matters most of all in every single long hot run")))
    r = go(fctx, sid, subj, merge=False)
    assert r["topics"] == 1 and r["dropped"] == 1


def test_a_quote_from_another_chunk_or_a_short_one_is_dropped(fctx, fake_or, src, subj):
    sid, no = src
    chunks = ts.chunks(fctx, sid)
    wrong = reply([("a", "A", no + 999, S1), ("b", "B", no, "Pacing a long run")])    # id not in the window; too short
    fake_or.chat_returns(wrong)
    r = go(fctx, sid, subj, merge=False)
    assert r["topics"] == 0 and r["dropped"] == 2 and chunks


def test_quote_check_ignores_case_and_spaces(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, ("pacing", "Pacing", "  PACING a long run  means starting slower than race pace so that  ")))
    assert go(fctx, sid, subj, merge=False)["topics"] == 1


def test_a_loop_is_refused_and_counted(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD, NEG, needs=[("pacing", "negative splits"), ("negative splits", "pacing")]))
    r = go(fctx, sid, subj, merge=False)
    assert r["loops"] == 1
    assert fctx.con.execute("select count(*) from teach_needs where status='active'").fetchone()[0] == 1


def test_needs_between_real_topics_are_added_and_unknown_names_ignored(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD, NEG, needs=[("negative splits", "pacing"), ("ghost", "pacing")]))
    go(fctx, sid, subj, merge=False)
    assert fctx.con.execute("select origin from teach_needs where status='active'").fetchall()[0]["origin"] == "model"
    assert fctx.con.execute("select count(*) from teach_needs").fetchone()[0] == 1


def test_a_model_link_never_overrides_one_he_made(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD, NEG))
    go(fctx, sid, subj, merge=False)
    tb.change(fctx, subj, "add_need", "negative splits", "pacing")
    tb.change(fctx, subj, "drop_need", "negative splits", "pacing")
    fake_or.chat_returns(topic_reply(no, GOOD, NEG, needs=[("negative splits", "pacing")]))
    go(fctx, sid, subj, merge=False)
    r = fctx.con.execute("select origin, status from teach_needs").fetchone()
    assert (r["origin"], r["status"]) == ("him", "dropped")


def test_a_topic_he_dropped_does_not_come_back(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD, NEG))
    go(fctx, sid, subj, merge=False)
    tb.change(fctx, subj, "drop_topic", "pacing")
    fake_or.chat_returns(topic_reply(no, GOOD, NEG))
    go(fctx, sid, subj, merge=False)
    assert fctx.con.execute("select status from teach_topics where key='pacing'").fetchone()[0] == "dropped"


def test_the_same_topic_twice_is_one_row(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD), times=2)
    go(fctx, sid, subj, merge=False)
    go(fctx, sid, subj, merge=False)
    assert fctx.con.execute("select count(*) from teach_topics").fetchone()[0] == 1


def test_the_merge_pass_merges_and_the_older_row_wins(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD, NEG, FUEL, needs=[("fuel", "pacing")]))
    fake_or.chat_returns('{"same": [[2, 3]]}')                   # negative splits and fuel are "the same" (a test pair)
    r = go(fctx, sid, subj)
    st = {x["key"]: x["status"] for x in fctx.con.execute("select key, status from teach_topics")}
    assert st == {"pacing": "active", "negative splits": "active", "fuel": "merged"} and r["merged"] == 1 and r["topics"] == 2


def test_merge_moves_needs_and_evidence_to_the_winner(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD, NEG, FUEL, needs=[("fuel", "pacing")]))
    fake_or.chat_returns('{"same": [[2, 3]]}')
    r = go(fctx, sid, subj)
    keep = fctx.con.execute("select id from teach_topics where key in ('negative splits','fuel') and status='active'").fetchone()[0]
    need = fctx.con.execute("select topic_id, needs_id, status from teach_needs where status='active'").fetchall()
    assert r["merged"] == 1 and len(need) == 1 and need[0]["topic_id"] == keep


def test_two_topics_where_one_needs_the_other_are_never_merged(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD, NEG, needs=[("negative splits", "pacing")]))
    fake_or.chat_returns('{"same": [[1, 2]]}')
    r = go(fctx, sid, subj)
    assert r["merged"] == 0 and r["topics"] == 2


def test_merge_pass_ignores_junk_pairs(fctx, fake_or, src, subj):
    sid, no = src
    fake_or.chat_returns(topic_reply(no, GOOD, NEG))
    fake_or.chat_returns('{"same": [[1, 1], [9, 2], ["a", 2], [1]]}')
    assert go(fctx, sid, subj)["merged"] == 0


def test_the_cap_stops_the_job(fctx, fake_or, src, subj, monkeypatch):
    sid, no = src
    monkeypatch.setattr(tb, "MAX_TOPICS", 2)
    fake_or.chat_returns(topic_reply(no, GOOD, NEG, FUEL))
    r = go(fctx, sid, subj)
    assert r["status"] == "too_big" and r["topics"] == 2 and "pick which part" in r["why"]
    assert len(fake_or.chat_bodies) == 1                          # no merge pass after the stop


def test_garbled_answers_fail_plainly(fctx, fake_or, src, subj):
    sid, _ = src
    fake_or.chat_returns("I could not do that")
    r = go(fctx, sid, subj)
    assert r["status"] == "failed" and "could not be read" in r["why"]


def test_a_spent_teach_budget_stops_with_a_reason_not_a_crash(fctx, fake_or, src, subj):
    sid, no = src
    fctx.settings.set("purpose_caps_daily", {"teach": 0.000001})
    fake_or.chat_returns(topic_reply(no, GOOD))
    r = go(fctx, sid, subj)
    assert r["status"] == "failed" and r["why"]
    assert fake_or.request_count == 0


def test_a_class_source_keeps_segment_ids(fctx, fake_or, subj):
    fctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status) VALUES ('cs_1','Python','Py',?, 'noted')", (fctx.clock.now() - 99,))
    ids = [fctx.con.execute("INSERT INTO class_segments(session_id,t_start,t_end,source,text) VALUES ('cs_1',?,?, 'room', ?)", (i, i + 1, t)).lastrowid
           for i, t in enumerate(["a loop repeats the same steps again and again until a stop test is met", "a list holds many items in a fixed order that can be changed"])]
    sid = run(ts.add(fctx, "cs_1"))["id"]
    fake_or.chat_returns(reply([("loops", "Loops", ids[1], "a list holds many items in a fixed order that can be changed")]))
    go(fctx, sid, subj, merge=False)
    assert f"[{ids[0]}] a loop repeats" in fake_or.chat_user_texts()[0]
    assert fctx.con.execute("select chunk_no from teach_topics").fetchone()[0] == ids[1]


# ---- the job -------------------------------------------------------------------------------------------------------
def test_job_is_registered_and_live():
    assert "teach_map" in pipeline.REGISTRY
    src = open(__file__.replace("tests/engine/test_teach_build.py", "engine/src/misty_engine/main.py")).read()
    assert '"teach_map", "teach_plan"' in src and '"teach_map": 1' in src and "build_kinds = [\"discover\", \"prep\", \"compile_batch\"]" in src


def test_queue_map_makes_a_job_once_and_the_handler_builds(fctx, fake_or, src):
    sid, no = src
    r = tb.queue_map(fctx, sid, "running", "Running")
    assert r["action"] == "queued" and tb.queue_map(fctx, sid, "running", "Running")["action"] == "already"
    j = fctx.con.execute("select * from jobs where kind='teach_map'").fetchall()
    assert len(j) == 1
    fake_or.chat_returns(topic_reply(no, GOOD))
    job = fctx.queue.claim(["teach_map"], 1)[0]
    run(pipeline.REGISTRY["teach_map"](job, fctx))
    assert tb.map_state(fctx, sid)["status"] == "done"


def test_teach_cap_and_setting():
    from misty_engine.config import DEFAULTS
    assert DEFAULTS["purpose_caps_daily"]["teach"] == 0.10 and "teach_budget" in setting.NAMES


async def yes(_): return True
async def no_(_): return False


def test_teach_budget_raise_asks_and_lowering_does_not(ctx):
    asked = []

    async def q(text):
        asked.append(text)
        return False
    r = run(setting.apply(ctx, "teach_budget", "0.20", q))
    assert r["declined"] and asked == ["Raise the teaching budget from $0.10 to $0.20 a day?"]
    assert ctx.settings.get("purpose_caps_daily")["teach"] == 0.10
    assert run(setting.apply(ctx, "teach_budget", "$0.20", yes))["verified"]
    assert ctx.settings.get("purpose_caps_daily")["teach"] == 0.20 and ctx.settings.get("purpose_caps_daily")["judge"] == 0.01
    assert run(setting.apply(ctx, "teach_budget", "0.05", no_))["verified"]
    for bad in ("0", "5", "lots", ""):
        assert run(setting.apply(ctx, "teach_budget", bad, yes))["ok"] is False


# ---- reading and fixing the map ------------------------------------------------------------------------------------
def test_ordered_puts_what_a_topic_needs_first(fctx, subj):
    c = {"source_id": "s", "chunk_no": 1, "quote": "q"}
    a, b, d = (tm.add_topic(fctx, subj, k, k, c)["id"] for k in ("alpha", "beta", "gamma"))
    tm.add_need(fctx, a, d)
    tm.add_need(fctx, a, b)
    order = [t["key"] for t in tb.ordered(fctx, subj)]
    assert order.index("alpha") > order.index("beta") and order.index("alpha") > order.index("gamma")
    assert tb.ordered(fctx, subj)[order.index("alpha")]["needs"] == ["gamma", "beta"]


def test_him_fixing_the_map(fctx, subj):
    c = {"source_id": "s", "chunk_no": 1, "quote": "q"}
    for k in ("alpha", "beta"):
        tm.add_topic(fctx, subj, k, k.title(), c)
    assert tb.change(fctx, subj, "add_topic", "Gamma rays")["action"] == "added"
    assert tb.change(fctx, subj, "add_topic", "Gamma rays")["action"] == "exists"
    assert tb.change(fctx, subj, "add_need", "alpha", "beta")["action"] == "need_added"
    assert fctx.con.execute("select origin from teach_needs").fetchone()[0] == "him"
    loop = tb.change(fctx, subj, "add_need", "beta", "alpha")
    assert loop["ok"] is False and "loop" in loop["error"]
    assert tb.change(fctx, subj, "drop_need", "alpha", "beta")["action"] == "need_dropped"
    assert tb.change(fctx, subj, "drop_topic", "Gamma rays")["action"] == "dropped"
    assert tb.change(fctx, subj, "add_topic", "gamma rays")["action"] == "back"
    with pytest.raises(Exception):
        tb.change(fctx, subj, "drop_topic", "nothing like it")
    with pytest.raises(Exception):
        tb.change(fctx, subj, "add_need", "alpha", "alpha")


def test_subject_lookup_by_stem_title_or_the_only_one(fctx):
    s = tm.add_subject(fctx, "half marathon", "Half marathon training")
    assert tb.find_subject(fctx, "Half Marathons")["id"] == s["id"]
    assert tb.find_subject(fctx, "marathon training")["id"] == s["id"]
    assert tb.find_subject(fctx, "")["id"] == s["id"]
    tm.add_subject(fctx, "spanish", "Spanish")
    assert tb.find_subject(fctx, "") is None and tb.find_subject(fctx, "chess") is None


# ---- the chat tool -------------------------------------------------------------------------------------------------
def call(ctx, args, source="voice"):
    async def status(_): ...
    async def confirm(*a, **k): return True
    return run(chat.run_tool(ctx, "course", args, on_status=status, confirm=confirm, source=source))


def test_tool_map_queues_then_show_lists_in_order(fctx, fake_or, src):
    sid, no = src
    r = call(fctx, {"action": "map", "what": "plan.txt", "subject": "Running"})
    assert r["ok"] and r["action"] == "queued" and "not done" in r["note"]
    fake_or.chat_returns(topic_reply(no, GOOD, NEG, needs=[("pacing", "negative splits")]))
    job = fctx.queue.claim(["teach_map"], 1)[0]
    run(pipeline.REGISTRY["teach_map"](job, fctx))
    s = call(fctx, {"action": "show", "subject": "running"})
    assert [t["topic"] for t in s["topics"]] == ["Negative splits", "Pacing a long run"] and s["map"] in ("done",)
    top = call(fctx, {"action": "show"})
    assert top["subjects"][0]["topics"] == 2 and top["sources"]


def test_tool_map_with_a_path_adds_the_source_then_maps(fctx, tmp_path):
    p = tmp_path / "other.txt"
    p.write_text(TEXT)
    r = call(fctx, {"action": "map", "what": str(p), "subject": "Other"})
    assert r["ok"] and fctx.con.execute("select count(*) from teach_sources").fetchone()[0] == 1


def test_tool_change_and_refusals(fctx, fake_or, src):
    sid, no = src
    tb.queue_map(fctx, sid, "running", "Running")
    fake_or.chat_returns(topic_reply(no, GOOD, NEG))
    run(pipeline.REGISTRY["teach_map"](fctx.queue.claim(["teach_map"], 1)[0], fctx))
    r = call(fctx, {"action": "change", "subject": "running", "change": "add_need", "topic": "negative splits", "needs": "pacing"})
    assert r["ok"] and r["verified"] and r["action"] == "need_added"
    assert call(fctx, {"action": "change", "subject": "chess", "change": "drop_topic", "topic": "x"})["ok"] is False
    assert call(fctx, {"action": "change", "subject": "running", "change": "drop_topic", "topic": "nothing"})["ok"] is False
    assert call(fctx, {"action": "map", "what": "plan.txt"}, source="text")["ok"] is False
    assert call(fctx, {"action": "change", "subject": "running", "change": "drop_topic", "topic": "pacing"}, source="text")["ok"] is False


def test_tool_text_and_prompt_name_the_new_actions():
    t = next(t for t in chat.TOOLS if t["function"]["name"] == "course")
    props = t["function"]["parameters"]["properties"]
    assert props["action"]["enum"][:4] == ["add_source", "show", "map", "change"]      # E14 adds make, pause, drop
    assert {"add_topic", "drop_topic", "add_need", "drop_need", "link_class"} <= set(props["change"]["enum"])
    assert "action map" in chat.COURSE_RULE and "drop_need" in chat.COURSE_RULE
