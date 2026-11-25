"""E15: the daily bite and re-planning. A fake model and a fake clock only, no paid call. A made-up stranger's subject."""
import asyncio
import json
import math
import random
from datetime import date, datetime, timedelta

import pytest

from misty_engine import brief, chat, gate, pipeline
from misty_engine.clock import SGT, sgt_date
from misty_engine.teach import bite, drill, match
from misty_engine.teach import course as tc
from misty_engine.teach import map as tm

CITE = {"source_id": "src1", "chunk_no": 0, "quote": "a short quote of at least five words"}


def run(coro):
    return asyncio.run(coro)


def at(day: str, h: int = 10, m: int = 0) -> float:
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=SGT).timestamp()


START = "2026-10-15"


def setup_clock(ctx, day=START, h=10):
    ctx.clock.t = at(day, h)
    gate.set_presence(ctx, 120.0, "idle")


def plus(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def make_subject(ctx, n=6, chain=False, key="physics", edges=()):
    s = tm.add_subject(ctx, key, key.title())["id"]
    ids = [tm.add_topic(ctx, s, f"topic {i}", f"Topic {i}", CITE)["id"] for i in range(n)]
    if chain:
        for i in range(1, n):
            tm.add_need(ctx, ids[i], ids[i - 1], "him")
    for a, b in edges:
        tm.add_need(ctx, ids[a], ids[b], "him")
    return s, ids


def make_course(ctx, s, by_days, per_day=None):
    by = plus(sgt_date(ctx.clock.now()), by_days)
    tc.propose(ctx, s, "ready", by, per_day)
    r = tc.activate(ctx, s)
    return r["course"]


def days_of(ctx, cid, kind="new", status="planned"):
    out = {}
    for r in ctx.con.execute("SELECT day, topic_id FROM teach_course_days WHERE course_id=? AND kind=? AND status=? ORDER BY day, topic_id", (cid, kind, status)):
        out.setdefault(r["day"], []).append(r["topic_id"])
    return out


def teach(ctx, topic_id):
    tm.record(ctx, topic_id, "taught", "lesson", "x")


def told(ctx):
    return [r["said"] for r in ctx.con.execute("SELECT said FROM told WHERE topic='teach' ORDER BY at")]


@pytest.fixture
def one(ctx):
    setup_clock(ctx)
    s, ids = make_subject(ctx, 6, chain=False)
    cid = make_course(ctx, s, 12)
    return s, ids, cid


# ---- the table ----------------------------------------------------------------------------------------------------
def test_table_exists(ctx):
    cols = [r[1] for r in ctx.con.execute("pragma table_info(teach_bite)")]
    for c in ("key", "at", "course_id", "day", "topic_id", "kind", "outcome"):
        assert c in cols


# ---- sync: what is done and what is missed -------------------------------------------------------------------------
def test_a_taught_topic_is_done_and_an_untaught_past_one_is_missed(ctx, one):
    s, ids, cid = one
    first = days_of(ctx, cid)
    d0 = min(first)
    t0 = first[d0][0]
    teach(ctx, t0)
    ctx.clock.t = at(plus(d0, 2))
    r = tc.sync(ctx, cid, plus(d0, 2))
    st = {x["topic_id"]: x["status"] for x in ctx.con.execute("select topic_id, status from teach_course_days where course_id=? and kind='new'", (cid,))}
    assert st[t0] == "done" and r["done"] >= 1
    assert any(v == "missed" for k, v in st.items() if k != t0)


def test_finishing_a_future_topic_early_marks_it_done_and_tomorrows_ask_for_it_goes(ctx, one):
    s, ids, cid = one
    later = sorted(days_of(ctx, cid))[3]
    t = days_of(ctx, cid)[later][0]
    teach(ctx, t)
    tc.sync(ctx, cid, START)
    st = ctx.con.execute("select status from teach_course_days where course_id=? and topic_id=? and kind='new'", (cid, t)).fetchone()[0]
    assert st == "done"
    assert t not in tc.today_items(ctx, cid, later)["new"]


def test_extra_work_today_does_not_remove_tomorrows_ask(ctx, one):
    s, ids, cid = one
    ds = sorted(days_of(ctx, cid))
    today_t, tomorrow_t = days_of(ctx, cid)[ds[0]][0], days_of(ctx, cid)[ds[1]][0]
    teach(ctx, today_t)
    tc.sync(ctx, cid, ds[0])
    assert tomorrow_t in tc.today_items(ctx, cid, ds[1])["new"]


def test_today_items_lists_what_is_planned_and_not_done(ctx, one):
    s, ids, cid = one
    ds = sorted(days_of(ctx, cid))
    items = tc.today_items(ctx, cid, ds[0])
    assert items["new"] == days_of(ctx, cid)[ds[0]]
    teach(ctx, items["new"][0])
    tc.sync(ctx, cid, ds[0])
    assert items["new"][0] not in tc.today_items(ctx, cid, ds[0])["new"]


# ---- the trigger ---------------------------------------------------------------------------------------------------
def mark(ctx, cid, day, topic, status):
    ctx.con.execute("INSERT OR REPLACE INTO teach_course_days(course_id, day, topic_id, kind, status) VALUES (?,?,?, 'new', ?)", (cid, day, topic, status))


def bare_course(ctx):
    s, ids = make_subject(ctx, 12, key="bare")
    ctx.con.execute("INSERT INTO teach_courses(id, subject_id, goal, by_date, per_day, status, proposed_json, created_at, updated_at) "
                    "VALUES ('co_t', ?, 'g', '2026-12-31', 1, 'active', '{}', 0, 0)", (s,))
    return "co_t", ids


def test_three_missed_days_in_a_row_trigger(ctx):
    setup_clock(ctx, "2026-10-20")
    cid, ids = bare_course(ctx)
    for i, st in enumerate(["done", "missed", "missed", "missed"]):
        mark(ctx, cid, plus("2026-10-15", i), ids[i], st)
    assert tc.missed_trigger(ctx, cid, "2026-10-20") is True


def test_two_missed_then_a_done_does_not(ctx):
    setup_clock(ctx, "2026-10-20")
    cid, ids = bare_course(ctx)
    for i, st in enumerate(["missed", "missed", "done", "missed"]):
        mark(ctx, cid, plus("2026-10-15", i), ids[i], st)
    assert tc.missed_trigger(ctx, cid, "2026-10-20") is False


def test_four_of_the_last_seven_trigger(ctx):
    setup_clock(ctx, "2026-10-22")
    cid, ids = bare_course(ctx)
    for i, st in enumerate(["missed", "done", "missed", "missed", "done", "missed"]):
        mark(ctx, cid, plus("2026-10-16", i), ids[i], st)
    assert tc.missed_trigger(ctx, cid, "2026-10-22") is True


def test_moved_days_are_not_missed(ctx):
    setup_clock(ctx, "2026-10-20")
    cid, ids = bare_course(ctx)
    for i, st in enumerate(["moved", "moved", "moved", "moved"]):
        mark(ctx, cid, plus("2026-10-15", i), ids[i], st)
    assert tc.missed_trigger(ctx, cid, "2026-10-20") is False


# ---- re-plan: a hand-checked table ---------------------------------------------------------------------------------
def replan_case(ctx, n, chain, by_days, advance, key):
    setup_clock(ctx)
    s, ids = make_subject(ctx, n, chain=chain, key=key)
    cid = make_course(ctx, s, by_days)
    today = plus(START, advance)
    ctx.clock.t = at(today)
    tc.sync(ctx, cid, today)
    r = tc.replan(ctx, cid, today)
    plan = days_of(ctx, cid)
    return r, {d: len(v) for d, v in plan.items()}, ids, plan


def test_table_a_six_free_topics_spread_over_the_days_left(ctx):
    r, per, ids, _ = replan_case(ctx, 6, False, 5, 3, "ta")         # d3 d4 d5 left, no free day: pace 2
    assert per == {plus(START, 3): 2, plus(START, 4): 2, plus(START, 5): 2} and r["over"] == []


def test_table_b_two_days_left_pace_three(ctx):
    r, per, ids, _ = replan_case(ctx, 6, False, 5, 4, "tb")
    assert per == {plus(START, 4): 3, plus(START, 5): 3} and r["over"] == []


def test_table_c_one_day_left_everything_on_it(ctx):
    r, per, ids, _ = replan_case(ctx, 6, False, 5, 5, "tc")
    assert per == {plus(START, 5): 6} and r["over"] == []


def test_table_d_deadline_passed_everything_is_listed(ctx):
    r, per, ids, _ = replan_case(ctx, 6, False, 5, 6, "td")
    assert per == {} and len(r["over"]) == 6 and r["cut"] == 6
    assert set(r["over"]) == {f"Topic {i}" for i in range(6)}


def test_table_e_a_chain_gets_one_a_day_and_the_rest_is_named(ctx):
    r, per, ids, plan = replan_case(ctx, 6, True, 9, 6, "te")        # today d6, by d9: 4 days, one chain topic a day
    assert per == {plus(START, 6): 1, plus(START, 7): 1, plus(START, 8): 1, plus(START, 9): 1}
    assert r["over"] == ["Topic 4", "Topic 5"] and r["cut"] == 2
    assert [plan[d][0] for d in sorted(plan)] == ids[:4]


def test_replan_marks_the_missed_rows_moved_so_it_does_not_fire_again(ctx):
    r, per, ids, _ = replan_case(ctx, 6, False, 5, 3, "tf")
    assert tc.missed_trigger(ctx, ctx.con.execute("select id from teach_courses").fetchone()[0], plus(START, 3)) is False
    assert ctx.con.execute("select count(*) from teach_course_days where status='missed' and kind='new'").fetchone()[0] == 0


def test_replan_never_puts_a_review_after_the_deadline(ctx):
    r, per, ids, plan = replan_case(ctx, 8, False, 9, 4, "tg")
    by = plus(START, 9)
    for row in ctx.con.execute("select day from teach_course_days"):
        assert row["day"] <= by


# ---- a simulated month: 200 seeds ----------------------------------------------------------------------------------
def check_invariants(ctx, cid, n_skip=0):
    c = ctx.con.execute("select * from teach_courses where id=?", (cid,)).fetchone()
    settings = json.loads(c["proposed_json"] or "{}")
    planned = {}
    for r in ctx.con.execute("select * from teach_course_days where course_id=? and status='planned'", (cid,)):
        planned.setdefault(r["kind"], []).append((r["day"], r["topic_id"]))
    by = c["by_date"]
    for kind, rows in planned.items():
        for day, _ in rows:
            assert day <= by, "a day past the deadline without being listed as over"
    per_day = {}
    for day, t in planned.get("new", []):
        per_day[day] = per_day.get(day, 0) + 1
    cap = math.ceil((c["per_day"] or 1) - 1e-9)
    assert all(v <= max(cap, 1) for v in per_day.values()), (per_day, c["per_day"])
    rev = {}
    for day, t in planned.get("review", []):
        rev[day] = rev.get(day, 0) + 1
    assert all(v <= tc.MAX_REVIEWS_PER_DAY for v in rev.values())
    day_of = {t: d for d, t in planned.get("new", [])}
    for r in ctx.con.execute("select n.topic_id, n.needs_id from teach_needs n"):
        a, b = r["topic_id"], r["needs_id"]
        if a in day_of and b in day_of:
            assert day_of[b] < day_of[a], "a topic is before something it needs"
    undone = {r[0] for r in ctx.con.execute("select topic_id from teach_course_days where course_id=? and kind='new' and status in ('planned')", (cid,))}
    return undone, set(settings.get("over") or [])


def test_a_simulated_month_never_breaks_the_rules(ctx):
    ran = 0
    for seed in range(200):
        rnd = random.Random(seed)
        today = plus(START, seed % 7)
        ctx.clock.t = at(today)
        gate.set_presence(ctx, 120.0, "idle")
        n = rnd.randint(5, 16)
        s, ids = make_subject(ctx, n, key=f"sim{seed}")
        for i in range(1, n):
            for j in rnd.sample(range(i), k=min(i, rnd.choice([0, 0, 1, 1, 2]))):
                tm.add_need(ctx, ids[i], ids[j], "him")
        by_days = rnd.randint(8, 30)
        cid = make_course(ctx, s, by_days)
        ran += 1
        miss_rate = rnd.choice([0.2, 0.5, 0.8])
        for step in range(by_days + 2):
            day = plus(today, step)
            ctx.clock.t = at(day)
            tc.sync(ctx, cid, day)
            if tc.missed_trigger(ctx, cid, day):
                tc.replan(ctx, cid, day)
            undone, over = check_invariants(ctx, cid)
            tc.sync(ctx, cid, day)
            items = tc.today_items(ctx, cid, day)["new"]
            if items and rnd.random() > miss_rate:
                teach(ctx, items[0])
        undone, over = check_invariants(ctx, cid)
    assert ran == 200


def test_every_undone_topic_is_planned_or_listed_as_over(ctx):
    for seed in range(40):
        rnd = random.Random(1000 + seed)
        setup_clock(ctx, plus(START, seed))
        n = rnd.randint(5, 14)
        s, ids = make_subject(ctx, n, chain=rnd.random() < 0.5, key=f"cov{seed}")
        cid = make_course(ctx, s, rnd.randint(4, 20))
        for step in range(1, 8):
            day = plus(sgt_date(ctx.clock.now()), 0)
            ctx.clock.t = at(plus(START, seed + step))
            day = sgt_date(ctx.clock.now())
            tc.sync(ctx, cid, day)
            if tc.missed_trigger(ctx, cid, day):
                tc.replan(ctx, cid, day)
        c = ctx.con.execute("select * from teach_courses where id=?", (cid,)).fetchone()
        over = set(json.loads(c["proposed_json"]).get("over") or [])
        planned = {r[0] for r in ctx.con.execute("select topic_id from teach_course_days where course_id=? and kind='new' and status='planned'", (cid,))}
        done = {r[0] for r in ctx.con.execute("select topic_id from teach_course_days where course_id=? and kind='new' and status='done'", (cid,))}
        missed = {r[0] for r in ctx.con.execute("select topic_id from teach_course_days where course_id=? and kind='new' and status='missed'", (cid,))}
        assert set(ids) - (planned | over | done | missed) == set(), "a topic was dropped without being listed"


# ---- the bite -------------------------------------------------------------------------------------------------------
def test_the_bite_is_offered_once_and_goes_through_the_gate(ctx, one):
    s, ids, cid = one
    r = run(bite.tick(ctx))
    assert len(r) == 1
    said = told(ctx)
    assert len(said) == 1 and said[0].startswith("Ten minutes on Topic ") and said[0].endswith("for Physics?")
    assert run(bite.tick(ctx)) == [] and len(told(ctx)) == 1
    row = ctx.con.execute("select topic, urgency from told where topic='teach'").fetchone()
    assert row["urgency"] == "soon"


def test_at_most_one_a_day_per_course_whatever_the_ticks(ctx, one):
    for h in range(7, 22):
        ctx.clock.t = at(START, h)
        gate.set_presence(ctx, 120.0, "idle")
        run(bite.tick(ctx))
        ctx.clock.advance(1000)
        run(bite.tick(ctx))
    assert len(told(ctx)) == 1


def test_one_a_day_across_a_week_and_two_courses(ctx, one):
    s2, ids2 = make_subject(ctx, 5, key="maths")
    make_course(ctx, s2, 12)
    for d in range(7):
        for h in (8, 12, 17):
            ctx.clock.t = at(plus(START, d), h)
            gate.set_presence(ctx, 120.0, "idle")
            run(bite.tick(ctx))
            ctx.clock.advance(400)
    per_day = {}
    for r in ctx.con.execute("select said, at from told where topic='teach'"):
        day = sgt_date(r["at"])
        per_day[day] = per_day.get(day, 0) + 1
    assert max(per_day.values()) <= 2                                           # two courses, one each, never two for the same course


def test_class_running_waits_and_is_not_recorded(ctx, one):
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status) VALUES ('cs_live','Phys','Phys',?, 'live')", (ctx.clock.now(),))
    assert run(bite.tick(ctx)) == [] and told(ctx) == []
    ctx.con.execute("UPDATE class_sessions SET status='ended', ended_at=?", (ctx.clock.now(),))
    assert len(run(bite.tick(ctx))) == 1                                         # offered once the class is over


def test_quiet_hours_wait(ctx, one):
    ctx.clock.t = at(START, 23, 30)
    gate.set_presence(ctx, 120.0, "idle")
    assert run(bite.tick(ctx)) == [] and told(ctx) == []
    ctx.clock.t = at(plus(START, 1), 8)
    gate.set_presence(ctx, 120.0, "idle")
    assert len(run(bite.tick(ctx))) == 1


def test_a_call_or_conversation_waits(ctx, one):
    gate.set_presence(ctx, 120.0, "speaking")
    assert run(bite.tick(ctx)) == []
    gate.set_presence(ctx, 120.0, "idle")
    assert len(run(bite.tick(ctx))) == 1


def test_the_daily_limit_holds_it_back(ctx, one):
    for i in range(30):
        ctx.con.execute("INSERT INTO told(id, at, topic, topic_text, key, text, said, how, urgency, why, waited_s, seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f"td{i}", ctx.clock.now() - 10 - i * 5000 / 30, "x", "x", f"k{i}", "t", "t", "say", "soon", "", 0, "{}"))
    assert run(bite.tick(ctx)) == [] and told(ctx) == []


def test_a_paused_or_dropped_course_is_not_offered(ctx, one):
    s, ids, cid = one
    tc.set_status(ctx, s, "paused")
    assert run(bite.tick(ctx)) == []
    tc.set_status(ctx, s, "dropped")
    assert run(bite.tick(ctx)) == []


def test_a_day_whose_topic_is_done_has_no_ask(ctx, one):
    s, ids, cid = one
    for t in days_of(ctx, cid)[min(days_of(ctx, cid))]:
        teach(ctx, t)
    assert run(bite.tick(ctx)) == []


def test_a_review_day_offers_a_drill_not_a_lesson(ctx):
    setup_clock(ctx)
    s, ids = make_subject(ctx, 2, key="rv")
    cid = make_course(ctx, s, 12)
    first = min(days_of(ctx, cid))
    revs = days_of(ctx, cid, kind="review")
    rd = min(revs)
    teach(ctx, revs[rd][0])
    for t in days_of(ctx, cid).get(rd, []):
        ctx.con.execute("update teach_course_days set status='done' where course_id=? and topic_id=? and kind='new'", (cid, t))
    ctx.con.execute("update teach_course_days set status='done' where course_id=? and kind='new'", (cid,))
    ctx.clock.t = at(rd)
    gate.set_presence(ctx, 120.0, "idle")
    run(bite.tick(ctx))
    said = told(ctx)
    assert said and said[-1].startswith("A quick drill on ")


def test_yes_is_a_lesson_on_that_topic_and_the_block_tells_her(ctx, one):
    s, ids, cid = one
    run(bite.tick(ctx))
    b = bite.block(ctx)
    topic = ctx.con.execute("select topic_id from teach_bite where outcome='told'").fetchone()[0]
    title = ctx.con.execute("select title from teach_topics where id=?", (topic,)).fetchone()[0]
    assert title in b and "teach" in b and "start" in b and "later" in b and "not_today" in b
    bite.accept(ctx)
    assert bite.block(ctx) == ""
    assert ctx.con.execute("select outcome from teach_bite where outcome like 'accepted'").fetchone()


def test_the_block_is_gone_after_two_hours(ctx, one):
    run(bite.tick(ctx))
    ctx.clock.advance(3 * 3600)
    assert bite.block(ctx) == ""


def test_later_keeps_it_planned_and_asks_once_more_in_the_evening(ctx, one):
    s, ids, cid = one
    run(bite.tick(ctx))
    r = bite.answer(ctx, "later")
    assert r["ok"] and bite.block(ctx) == ""
    ctx.clock.t = at(START, 14)
    gate.set_presence(ctx, 120.0, "idle")
    assert run(bite.tick(ctx)) == []                                              # not yet evening
    ctx.clock.t = at(START, 19)
    gate.set_presence(ctx, 120.0, "idle")
    assert len(run(bite.tick(ctx))) == 1 and told(ctx)[-1].startswith("Still")
    ctx.clock.advance(5000)
    gate.set_presence(ctx, 120.0, "idle")
    assert run(bite.tick(ctx)) == [] and len(told(ctx)) == 2


def test_not_today_marks_the_day_missed_and_no_more_that_day(ctx, one):
    s, ids, cid = one
    run(bite.tick(ctx))
    t = days_of(ctx, cid)[min(days_of(ctx, cid))][0]
    r = bite.answer(ctx, "not_today")
    assert r["ok"]
    st = ctx.con.execute("select status from teach_course_days where course_id=? and topic_id=? and kind='new'", (cid, t)).fetchone()[0]
    assert st == "missed"
    ctx.clock.t = at(START, 19)
    gate.set_presence(ctx, 120.0, "idle")
    assert run(bite.tick(ctx)) == []


def test_answer_with_nothing_offered_says_so(ctx, one):
    assert bite.answer(ctx, "later")["ok"] is False


def test_tomorrows_topic_gets_a_plan_the_evening_before(ctx, one):
    s, ids, cid = one
    ctx.con.execute("delete from jobs")
    tomorrow = days_of(ctx, cid)[sorted(days_of(ctx, cid))[1]]
    ctx.clock.t = at(START, 19)
    gate.set_presence(ctx, 120.0, "idle")
    run(bite.tick(ctx))
    queued = {json.loads(r["payload"])["topic_id"] for r in ctx.con.execute("select payload from jobs where kind='teach_plan'")}
    assert set(tomorrow) <= queued


def test_three_missed_days_replan_and_say_what_was_cut_through_the_gate(ctx):
    setup_clock(ctx)
    s, ids = make_subject(ctx, 6, chain=True, key="rp")
    cid = make_course(ctx, s, 9)
    ctx.clock.t = at(plus(START, 6))
    gate.set_presence(ctx, 120.0, "idle")
    run(bite.tick(ctx))
    texts = told(ctx)
    cut = [x for x in texts if x.startswith("I moved things around")]
    assert len(cut) == 1 and "2 topics no longer fit" in cut[0] and "Topic 4" in cut[0] and "Topic 5" in cut[0] and cut[0].endswith("Cut them or move the date?")
    ctx.clock.t = at(plus(START, 6), 15)
    gate.set_presence(ctx, 120.0, "idle")
    run(bite.tick(ctx))
    assert len([x for x in told(ctx) if x.startswith("I moved things around")]) == 1       # said once


def test_a_replan_that_fits_says_nothing_aloud(ctx):
    setup_clock(ctx)
    s, ids = make_subject(ctx, 6, key="rq")
    cid = make_course(ctx, s, 5)
    ctx.clock.t = at(plus(START, 3))
    gate.set_presence(ctx, 120.0, "idle")
    run(bite.tick(ctx))
    assert not [x for x in told(ctx) if x.startswith("I moved things")]
    notes = [r["text"] for r in ctx.con.execute("select text from chat_messages where meta like '%\"note\"%'")]
    assert any("spread" in n or "moved" in n for n in notes)


# ---- scene 9h: fill the gap -----------------------------------------------------------------------------------------
def add_class(ctx, sid, subject, next_step, ended, status="noted"):
    notes = {"next_step": {"text": next_step, "seg": [1]} if next_step else None}
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, ended_at, status, notes_json) VALUES (?,?,?,?,?,?,?)",
                    (sid, subject, subject, ended - 3000, ended, status, json.dumps(notes)))


FIVE = [("chain rule", 3), ("the mole", 1), ("vectors", 4), ("moments", 2), ("friction", 0)]


@pytest.mark.parametrize("phrase,idx", FIVE)
def test_the_class_next_step_makes_the_right_topic_jump(ctx, monkeypatch, phrase, idx):
    monkeypatch.setattr(ctx, "paid_allowed", lambda: True)
    setup_clock(ctx)
    s, ids = make_subject(ctx, 6, key="phys")
    tm.link_class(ctx, s, "Phys")
    cid = make_course(ctx, s, 20)
    add_class(ctx, "cs_a", "Phys", f"Go over {phrase} before Thursday", ctx.clock.now() - 3600)
    calls = []

    async def fake(ctx_, subject_id, text):
        calls.append(text)
        return ids[idx] if phrase in text else None
    monkeypatch.setattr(match, "topic_for", fake)
    tm.record(ctx, ids[idx], "class_lost", "class", "cs_a")                      # he got lost there: shaky
    run(bite.tick(ctx))
    said = told(ctx)
    assert len(said) == 1 and f"Topic {idx}" in said[0] and "class" in said[0].lower() and "Ten minutes now?" in said[0]
    today = sgt_date(ctx.clock.now())
    assert ids[idx] in days_of(ctx, cid)[today]
    run(bite.tick(ctx))
    assert len(calls) == 1                                                       # matched once per class, not every minute


def test_a_solid_topic_does_not_jump(ctx, monkeypatch):
    monkeypatch.setattr(ctx, "paid_allowed", lambda: True)
    setup_clock(ctx)
    s, ids = make_subject(ctx, 6, key="phys")
    tm.link_class(ctx, s, "Phys")
    cid = make_course(ctx, s, 20)
    add_class(ctx, "cs_a", "Phys", "Go over the mole", ctx.clock.now() - 3600)
    for _ in range(3):
        tm.record(ctx, ids[3], "right_alone", "drill", "x")
    ctx.clock.advance(1)
    tm.record(ctx, ids[3], "right_alone", "drill", "y")

    async def fake(ctx_, subject_id, text):
        return ids[3]
    monkeypatch.setattr(match, "topic_for", fake)
    run(bite.tick(ctx))
    said = told(ctx)
    assert len(said) == 1 and "class" not in said[0].lower() and said[0].startswith("Ten minutes on")


def test_the_same_class_and_topic_is_said_once(ctx, monkeypatch):
    monkeypatch.setattr(ctx, "paid_allowed", lambda: True)
    setup_clock(ctx)
    s, ids = make_subject(ctx, 6, key="phys")
    tm.link_class(ctx, s, "Phys")
    make_course(ctx, s, 20)
    add_class(ctx, "cs_a", "Phys", "Go over the chain rule", ctx.clock.now() - 3600)
    tm.record(ctx, ids[3], "class_lost", "class", "cs_a")

    async def fake(ctx_, subject_id, text):
        return ids[3]
    monkeypatch.setattr(match, "topic_for", fake)
    run(bite.tick(ctx))
    tm.record(ctx, ids[3], "wrong", "drill", "x")
    ctx.clock.t = at(plus(START, 1))
    gate.set_presence(ctx, 120.0, "idle")
    run(bite.tick(ctx))
    class_lines = [x for x in told(ctx) if "class" in x.lower()]
    assert len(class_lines) == 1


def test_a_topic_whose_need_is_unknown_does_not_jump(ctx, monkeypatch):
    monkeypatch.setattr(ctx, "paid_allowed", lambda: True)
    setup_clock(ctx)
    s, ids = make_subject(ctx, 6, key="phys", edges=[(3, 2)])
    tm.link_class(ctx, s, "Phys")
    make_course(ctx, s, 20)
    add_class(ctx, "cs_a", "Phys", "Go over the chain rule", ctx.clock.now() - 3600)

    async def fake(ctx_, subject_id, text):
        return ids[3]
    monkeypatch.setattr(match, "topic_for", fake)
    run(bite.tick(ctx))
    assert "class" not in told(ctx)[0].lower()


def test_an_old_class_or_one_with_no_next_step_is_ignored(ctx, monkeypatch):
    monkeypatch.setattr(ctx, "paid_allowed", lambda: True)
    setup_clock(ctx)
    s, ids = make_subject(ctx, 6, key="phys")
    tm.link_class(ctx, s, "Phys")
    make_course(ctx, s, 20)
    add_class(ctx, "cs_old", "Phys", "Go over the chain rule", ctx.clock.now() - 9 * 86400)
    add_class(ctx, "cs_none", "Phys", "", ctx.clock.now() - 3600)
    calls = []

    async def fake(ctx_, subject_id, text):
        calls.append(text)
        return ids[3]
    monkeypatch.setattr(match, "topic_for", fake)
    run(bite.tick(ctx))
    assert calls == [] and "class" not in told(ctx)[0].lower()


# ---- the brief and work_on -----------------------------------------------------------------------------------------
def test_today_facts_name_the_days_topic(ctx, one):
    f = bite.today_facts(ctx)
    assert len(f) == 1 and f[0]["course"] == "Physics" and f[0]["topic"].startswith("Topic ") and f[0]["do"] == "lesson"


def test_no_course_no_facts(ctx):
    assert bite.today_facts(ctx) == []


def test_work_on_and_the_brief_know_the_days_topic(ctx, one):
    g = run(brief.gather(ctx))
    assert g["facts"]["teach_today"][0]["topic"].startswith("Topic ")
    assert "teach_today" in brief.PROMPT


# ---- wiring ---------------------------------------------------------------------------------------------------------
def test_the_tick_is_scheduled_every_minute():
    import inspect
    from misty_engine import scheduler
    s = inspect.getsource(scheduler)
    assert '"bite"' in s and "bite.tick" in s


def test_teach_tool_has_bite_and_the_answer_arg():
    t = next(x for x in chat.TOOLS if x["function"]["name"] == "teach")
    props = t["function"]["parameters"]["properties"]
    assert "bite" in props["action"]["enum"] and "answer" in props
    assert props["answer"]["enum"] == ["later", "not_today"]


def test_the_tool_answers_through_chat(ctx, one):
    run(bite.tick(ctx))

    async def st(_): ...
    async def cf(*a, **k): return True
    r = run(chat.run_tool(ctx, "teach", {"action": "bite", "answer": "later"}, on_status=st, confirm=cf))
    assert r["ok"]


def test_a_drill_can_start_on_a_named_topic(ctx, monkeypatch):
    setup_clock(ctx)
    s, ids = make_subject(ctx, 3, key="dd")
    seen = {}

    async def fake_start(ctx_, subject_id=None, topic_id=None, quick=False, mine=False):
        seen.update(topic=topic_id)
        return {"ok": True}
    monkeypatch.setattr(drill, "start", fake_start)
    run(drill.tool(ctx, {"action": "drill", "topic": "Topic 1", "subject": "dd"}))
    assert seen["topic"] == ids[1]
