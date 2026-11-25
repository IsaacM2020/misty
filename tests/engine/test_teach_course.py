"""E14: a course by talking. Pure layout (property tested), his dates in words, the tool, and a plan that is never saved without a yes.
Fake model, no paid call. A made-up stranger's subject, nothing about one person."""
import asyncio
import json
import math
import random
from datetime import date, timedelta

import pytest

from misty_engine import chat
from misty_engine.clock import sgt_date
from misty_engine.teach import course as tc
from misty_engine.teach import map as tm

CITE = {"source_id": "src1", "chunk_no": 0, "quote": "a short quote of at least five words"}


def run(coro):
    return asyncio.run(coro)


def d(s):
    return date.fromisoformat(s)


# ---- his dates in words ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expect", [
    ("2027-05-31", "2027-05-31"), ("by May", "2027-05-31"), ("end of May", "2027-05-31"), ("June", "2027-06-30"),
    ("15 June", "2027-06-15"), ("June 15", "2027-06-15"), ("in 3 weeks", "2026-11-21"), ("in 10 days", "2026-11-10"),
    ("in 2 months", "2026-12-31"), ("next month", "2026-11-30"), ("December", "2026-12-31"), ("1 Nov", "2026-11-01"), ("whenever", None), ("", None),
])
def test_parse_by(text, expect):
    assert tc.parse_by(text, "2026-10-31") == expect


# ---- layout ---------------------------------------------------------------------------------------------------------
def topics(n, chain=False):
    return [{"topic_id": f"t{i}", "needs": ([f"t{i - 1}"] if chain and i else [])} for i in range(n)]


def newdays(lay):
    return {t: x["day"] for x in lay["days"] for t in x["new"]}


def test_auto_pace_fits_exactly_and_keeps_a_fifth_of_the_days_free():
    lay = tc.layout(topics(8), {}, "2026-11-01", "2026-11-10", None)
    assert lay["over"] == [] and lay["per_day"] == 1 and len(lay["free"]) == 2
    nd = newdays(lay)
    assert len(nd) == 8 and not set(nd.values()) & set(lay["free"]) and max(nd.values()) <= "2026-11-10"


def test_a_plan_that_does_not_fit_says_which_topics_are_over_and_what_it_costs():
    lay = tc.layout(topics(10), {}, "2026-11-01", "2026-11-05", 1)
    assert len(lay["over"]) == 6 and lay["over"] == [f"t{i}" for i in range(4, 10)]
    s = tc.summary_text(lay, 10, "2026-11-05")
    assert "4 of 10" in s and "cut 6" in s and "move the date" in s


def test_solid_and_mastered_topics_and_skipped_ones_are_not_scheduled():
    lay = tc.layout(topics(5), {"t0": "solid", "t1": "mastered", "t2": "shaky"}, "2026-11-01", "2026-11-20", None, skip={"t3"})
    assert set(newdays(lay)) == {"t2", "t4"}


def test_a_need_is_always_on_an_earlier_day():
    lay = tc.layout(topics(6, chain=True), {}, "2026-11-01", "2026-11-30", 3)
    nd = newdays(lay)
    assert all(nd[f"t{i - 1}"] < nd[f"t{i}"] for i in range(1, 6))


def test_reviews_follow_the_review_days_and_at_most_two_a_day():
    lay = tc.layout(topics(4), {}, "2026-11-01", "2026-12-31", 2)
    nd = newdays(lay)
    for t, day in nd.items():
        got = sorted(x["day"] for x in lay["days"] if t in x["review"])
        assert len(got) == 5
        for k, off in enumerate(tm.REVIEW_DAYS):
            assert d(got[k]) >= d(day) + timedelta(days=off)
    assert all(len(x["review"]) <= 2 for x in lay["days"])


def test_a_review_past_the_deadline_is_dropped_not_over():
    lay = tc.layout(topics(2), {}, "2026-11-01", "2026-11-04", 1)
    assert lay["over"] == [] and all(x["day"] <= "2026-11-04" for x in lay["days"])


def test_no_date_makes_a_horizon_from_the_pace():
    lay = tc.layout(topics(6), {}, "2026-11-01", None, 2)
    assert lay["over"] == [] and lay["by"] >= "2026-11-03"


def test_half_a_topic_a_day_is_every_other_working_day():
    lay = tc.layout(topics(4), {}, "2026-11-01", "2026-11-30", 0.5)
    nd = sorted(d(x) for x in newdays(lay).values())
    assert len(nd) == 4 and all((b - a).days >= 2 for a, b in zip(nd, nd[1:]))


def test_by_in_the_past_puts_everything_over():
    lay = tc.layout(topics(3), {}, "2026-11-10", "2026-11-01", None)
    assert len(lay["over"]) == 3 and lay["days"] == []


# ---- the property test: 200 random graphs ------------------------------------------------------------------------------
def test_property_200_random_courses():
    rnd = random.Random(14)
    for case in range(200):
        n = rnd.randint(0, 40)
        tops = [{"topic_id": f"t{i}", "needs": [f"t{j}" for j in rnd.sample(range(i), min(i, rnd.randint(0, 3)))]} for i in range(n)]
        levels = {f"t{i}": rnd.choice(["solid", "mastered"]) for i in range(n) if rnd.random() < 0.15}
        skip = {f"t{i}" for i in range(n) if rnd.random() < 0.1}
        today = date(2026, 11, 1) + timedelta(days=rnd.randint(0, 20))
        by = None if rnd.random() < 0.1 else (today + timedelta(days=rnd.randint(-2, 70))).isoformat()
        per = rnd.choice([None, None, 1, 2, 3, 0.5])
        lay = tc.layout(tops, levels, today.isoformat(), by, per, skip=skip)
        nd = newdays(lay)
        wanted = {t["topic_id"] for t in tops} - set(levels) - skip
        assert set(nd) | set(lay["over"]) == wanted and not set(nd) & set(lay["over"]), case     # every topic is scheduled or over, never both
        assert not set(nd) & (set(levels) | skip), case
        end = lay["by"]
        for t in tops:
            if t["topic_id"] not in nd:
                continue
            for need in t["needs"]:
                if need in nd:
                    assert nd[need] < nd[t["topic_id"]], (case, t)                               # nothing before something it needs
                elif need in wanted:
                    pytest.fail(f"{t['topic_id']} is scheduled but its need {need} is not ({case})")
        for x in lay["days"]:
            assert today.isoformat() <= x["day"] <= end, case                                    # nothing past by without over saying so
            assert x["day"] not in lay["free"] and len(x["new"]) <= max(1, math.ceil(lay["per_day"])), case
            assert len(x["review"]) <= 2 and not set(x["new"]) & set(x["review"]), case
        for t, day in nd.items():
            got = sorted(x["day"] for x in lay["days"] if t in x["review"])
            assert len(got) <= 5, case
            for k, dd in enumerate(got):
                assert d(dd) >= d(day) + timedelta(days=tm.REVIEW_DAYS[k]), case               # reviews follow REVIEW_DAYS
        if by is not None and d(by) < today:
            assert lay["days"] == [], case


# ---- the tool: ten spoken-style requests as scripted calls -------------------------------------------------------------
@pytest.fixture
def subj(ctx):
    s = tm.add_subject(ctx, "physics hl", "Physics HL")["id"]
    ids = [tm.add_topic(ctx, s, f"topic {i}", f"Topic {i}", CITE)["id"] for i in range(12)]
    tm.add_need(ctx, ids[1], ids[0], "him")
    tm.add_need(ctx, ids[2], ids[1], "him")
    return s


def call(ctx, **args):
    async def st(_): ...
    async def cf(*a, **k): return True
    return run(chat.run_tool(ctx, "course", args, on_status=st, confirm=cf))


def today(ctx):
    return sgt_date(ctx.clock.now())


def rows(ctx, status=None):
    q = "select * from teach_courses" + (f" where status='{status}'" if status else "")
    return ctx.con.execute(q).fetchall()


def test_make_proposes_and_saves_nothing(ctx, subj):
    r = call(ctx, action="make", subject="physics hl", goal="ready for Physics HL", by="in 6 weeks")
    assert r["ok"] and r["proposed"] and "topics" in r["summary"]
    assert [x["status"] for x in rows(ctx)] == ["proposed"]
    assert ctx.con.execute("select count(*) from teach_course_days").fetchone()[0] == 0


def test_a_yes_saves_it_active_with_days_and_queues_plans(ctx, subj):
    call(ctx, action="make", subject="physics hl", goal="ready", by="in 6 weeks")
    r = call(ctx, action="make", subject="physics hl", confirm=True)
    assert r["ok"] and r["status"] == "active"
    c = rows(ctx)[0]
    assert c["status"] == "active" and c["by_date"] == (d(today(ctx)) + timedelta(days=42)).isoformat()
    assert ctx.con.execute("select count(*) from teach_course_days where course_id=? and kind='new'", (c["id"],)).fetchone()[0] == 12
    assert ctx.con.execute("select count(*) from jobs where kind='teach_plan'").fetchone()[0] >= 3


def test_confirm_without_a_proposal_saves_nothing(ctx, subj):
    r = call(ctx, action="make", subject="physics hl", confirm=True)
    assert r["ok"] is False and rows(ctx) == []


def test_a_new_make_replaces_the_old_proposal(ctx, subj):
    call(ctx, action="make", subject="physics hl", goal="a", by="in 3 weeks")
    call(ctx, action="make", subject="physics hl", goal="b", by="in 5 weeks")
    p = rows(ctx, "proposed")
    assert len(p) == 1 and p[0]["goal"] == "b"


def test_pace_slower_halves_and_deadline_moves_and_skip_marks(ctx, subj):
    call(ctx, action="make", subject="physics hl", goal="g", by="in 8 weeks")
    call(ctx, action="make", subject="physics hl", confirm=True)
    before = rows(ctx)[0]["per_day"]
    r = call(ctx, action="change", subject="physics hl", change="pace", pace="slower")
    assert r["ok"] and rows(ctx)[0]["per_day"] == pytest.approx(max(0.25, before / 2))
    r = call(ctx, action="change", subject="physics hl", change="deadline", by="in 12 weeks")
    assert rows(ctx)[0]["by_date"] == (d(today(ctx)) + timedelta(days=84)).isoformat()
    r = call(ctx, action="change", subject="physics hl", change="skip_topic", topic="Topic 5")
    assert r["ok"]
    skipped = ctx.con.execute("select id from teach_topics where title='Topic 5'").fetchone()[0]
    assert ctx.con.execute("select count(*) from teach_course_days where topic_id=?", (skipped,)).fetchone()[0] == 0
    assert ctx.con.execute("select count(*) from teach_needs where status='active'").fetchone()[0] == 2     # his needs stay


def test_change_re_lays_out_only_days_not_done(ctx, subj):
    call(ctx, action="make", subject="physics hl", goal="g", by="in 8 weeks")
    call(ctx, action="make", subject="physics hl", confirm=True)
    c = rows(ctx)[0]["id"]
    first = ctx.con.execute("select * from teach_course_days where course_id=? and kind='new' order by day limit 1", (c,)).fetchone()
    ctx.con.execute("update teach_course_days set status='done' where course_id=? and topic_id=? and day=?", (c, first["topic_id"], first["day"]))
    call(ctx, action="change", subject="physics hl", change="pace", pace="slower")
    still = ctx.con.execute("select status from teach_course_days where course_id=? and topic_id=? and kind='new'", (c, first["topic_id"])).fetchall()
    assert [x[0] for x in still] == ["done"]


def test_pause_keeps_the_plan_and_drop_keeps_the_days_for_the_record(ctx, subj):
    call(ctx, action="make", subject="physics hl", goal="g", by="in 8 weeks")
    call(ctx, action="make", subject="physics hl", confirm=True)
    n = ctx.con.execute("select count(*) from teach_course_days").fetchone()[0]
    assert call(ctx, action="pause", subject="physics hl")["ok"] and rows(ctx)[0]["status"] == "paused"
    assert call(ctx, action="drop", subject="physics hl")["ok"] and rows(ctx)[0]["status"] == "dropped"
    assert ctx.con.execute("select count(*) from teach_course_days").fetchone()[0] == n


def test_an_over_plan_says_so_in_the_summary(ctx, subj):
    r = call(ctx, action="make", subject="physics hl", goal="g", by="in 4 days")
    assert r["ok"] and "move the date" in r["summary"] and r["over"] > 0


def test_a_file_with_no_subject_adds_the_source_and_starts_the_map(ctx, tmp_path):
    p = tmp_path / "syllabus.txt"
    p.write_text("Forces and motion. " * 40)
    r = call(ctx, action="make", what=str(p), goal="ready for it", by="in 6 weeks")
    assert r["ok"] and r.get("map_started") and rows(ctx) == []
    assert ctx.con.execute("select count(*) from jobs where kind='teach_map'").fetchone()[0] == 1


def test_nothing_to_teach_from_says_what_she_needs(ctx):
    r = call(ctx, action="make", goal="ready", by="in 6 weeks")
    assert r["ok"] is False and "syllabus" in r["error"] and "find one" in r["error"]


def test_no_date_and_no_pace_asks_by_when(ctx, subj):
    r = call(ctx, action="make", subject="physics hl", goal="g")
    assert r["ok"] is False and "by when" in r["error"]


def test_change_without_a_course_is_refused(ctx, subj):
    assert call(ctx, action="change", subject="physics hl", change="pace", pace="slower")["ok"] is False


def test_text_she_read_cannot_make_or_drop_a_course(ctx, subj):
    async def st(_): ...
    async def cf(*a, **k): return True
    for act in ("make", "pause", "drop"):
        r = run(chat.run_tool(ctx, "course", {"action": act, "subject": "physics hl", "by": "in 3 weeks"}, on_status=st, confirm=cf, source="text"))
        assert r["ok"] is False
    assert rows(ctx) == []


SPOKEN = [  # (the calls she would make, then what the saved course must look like)
    ([dict(action="make", subject="physics hl", goal="ready for Physics HL", by="in 8 weeks"), dict(action="make", subject="physics hl", confirm=True)], {"status": "active", "by": 56}),
    ([dict(action="make", subject="physics hl", goal="from zero", by="in 10 weeks"), dict(action="make", subject="physics hl", confirm=True)], {"status": "active", "by": 70}),
    ([dict(action="make", subject="physics hl", goal="g", by="in 8 weeks"), dict(action="make", subject="physics hl", confirm=True),
      dict(action="change", subject="physics hl", change="pace", pace="slower")], {"status": "active", "by": 56, "slower": True}),
    ([dict(action="make", subject="physics hl", goal="g", by="in 8 weeks"), dict(action="make", subject="physics hl", confirm=True),
      dict(action="change", subject="physics hl", change="skip_topic", topic="Topic 7")], {"status": "active", "by": 56, "skip": "Topic 7"}),
    ([dict(action="make", subject="physics hl", goal="g", by="in 8 weeks"), dict(action="make", subject="physics hl", confirm=True),
      dict(action="change", subject="physics hl", change="deadline", by="in 12 weeks")], {"status": "active", "by": 84}),
    ([dict(action="make", subject="physics hl", goal="g", by="in 8 weeks"), dict(action="make", subject="physics hl", confirm=True),
      dict(action="pause", subject="physics hl")], {"status": "paused", "by": 56}),
    ([dict(action="make", subject="physics hl", goal="g", by="in 8 weeks"), dict(action="make", subject="physics hl", confirm=True),
      dict(action="drop", subject="physics hl")], {"status": "dropped", "by": 56}),
    ([dict(action="make", subject="physics hl", goal="g", by="in 8 weeks")], {"status": "proposed", "by": 56}),
    ([dict(action="make", subject="physics hl", goal="g", by="in 8 weeks"), dict(action="make", subject="physics hl", goal="h", by="in 5 weeks"),
      dict(action="make", subject="physics hl", confirm=True)], {"status": "active", "by": 35}),
    ([dict(action="make", subject="physics hl", goal="g", by="in 8 weeks"), dict(action="make", subject="physics hl", confirm=True),
      dict(action="change", subject="physics hl", change="pace", pace="slower"), dict(action="change", subject="physics hl", change="pace", pace="faster")], {"status": "active", "by": 56}),
]


def test_ten_spoken_requests_save_what_was_asked(ctx, subj):
    ok = 0
    for i, (calls, want) in enumerate(SPOKEN):
        ctx.con.execute("delete from teach_course_days")
        ctx.con.execute("delete from teach_courses")
        for c in calls:
            call(ctx, **c)
        r = rows(ctx)
        c = r[-1] if r else None
        good = c is not None and c["status"] == want["status"] and c["by_date"] == (d(today(ctx)) + timedelta(days=want["by"])).isoformat()
        if good and want.get("skip"):
            good = want["skip"] in call(ctx, action="show", subject="physics hl").get("skipped", [])
        ok += bool(good)
    assert ok >= 9, ok


def test_the_tool_text_has_the_new_actions_and_the_rule_is_in_the_normal_prompt_only(ctx):
    t = next(t for t in chat.TOOLS if t["function"]["name"] == "course")["function"]["parameters"]["properties"]
    assert {"make", "pause", "drop"} <= set(t["action"]["enum"]) and {"skip_topic", "pace", "deadline"} <= set(t["change"]["enum"])
    assert {"goal", "by", "confirm", "pace"} <= set(t)
    normal = "".join(chat.system_parts(ctx, "hi"))
    exam = "".join(chat.system_parts(ctx, "hi", exam=True))
    assert "confirm" in chat.COURSE_RULE and chat.COURSE_RULE.strip() in normal and chat.COURSE_RULE.strip() not in exam
