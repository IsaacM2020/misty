"""E1: the map's stores and the exact scoring. Pure code, no model. Every rule is pinned with numbers."""
import pytest

from misty_engine.errors import Fatal
from misty_engine.teach import map as tm

DAY = 86400.0
CITE = {"source_id": "src1", "chunk_no": 3, "quote": "force equals mass times acceleration"}


@pytest.fixture
def subj(ctx):
    return tm.add_subject(ctx, "Half marathon", "Half marathon training")["id"]


def topic(ctx, subj, key, title=None):
    return tm.add_topic(ctx, subj, key, title or key, CITE)["id"]


def ev(ctx, tid, kind, ago_days=0.0, now=None):
    """Record evidence `ago_days` before `now` (default: the clock's now)."""
    now = ctx.clock.now() if now is None else now
    t0 = ctx.clock.t
    ctx.clock.t = now - ago_days * DAY
    try:
        return tm.record(ctx, tid, kind, "lesson", "l1")
    finally:
        ctx.clock.t = t0


# ---- migration ---------------------------------------------------------------------------------------------------
def test_tables_exist(con):
    for t in ("teach_subjects", "teach_topics", "teach_needs", "teach_evidence", "teach_slips"):
        assert con.execute("select count(*) from sqlite_master where name=?", (t,)).fetchone()[0] == 1
    assert 26 in [r[0] for r in con.execute("select version from schema_version")]


# ---- topics and subjects -----------------------------------------------------------------------------------------
def test_same_key_or_same_stem_updates_the_old_topic(ctx, subj):
    a = tm.add_topic(ctx, subj, "Long Runs", "Long runs", CITE)
    b = tm.add_topic(ctx, subj, "long run", "The long run", {**CITE, "chunk_no": 9})
    assert a["id"] == b["id"] and b["action"] == "updated"
    rows = ctx.con.execute("select * from teach_topics").fetchall()
    assert len(rows) == 1 and rows[0]["title"] == "The long run" and rows[0]["chunk_no"] == 9


def test_same_key_in_another_subject_is_a_different_topic(ctx, subj):
    other = tm.add_subject(ctx, "Python", "Python")["id"]
    assert topic(ctx, subj, "pacing") != topic(ctx, other, "pacing")


def test_a_cite_is_stored_and_a_bad_one_refused(ctx, subj):
    tid = topic(ctx, subj, "pacing")
    r = ctx.con.execute("select * from teach_topics where id=?", (tid,)).fetchone()
    assert (r["source_id"], r["chunk_no"], r["quote"], r["status"]) == ("src1", 3, CITE["quote"], "active")
    with pytest.raises(Fatal):
        tm.add_topic(ctx, subj, "x", "x", {"source_id": "", "chunk_no": 1, "quote": "q"})
    with pytest.raises(Fatal):
        tm.add_topic(ctx, subj, "", "x", CITE)


def test_a_subject_is_added_once_per_key(ctx):
    a = tm.add_subject(ctx, "Cricket Analytics", "Cricket analytics")
    b = tm.add_subject(ctx, "cricket analytic", "Cricket analytics")
    assert a["id"] == b["id"] and ctx.con.execute("select count(*) from teach_subjects").fetchone()[0] == 1


# ---- needs and loops ---------------------------------------------------------------------------------------------
def test_a_need_is_added_and_repeated_is_one_row(ctx, subj):
    a, b = topic(ctx, subj, "a"), topic(ctx, subj, "b")
    assert tm.add_need(ctx, a, b, "model")["ok"] is True
    assert tm.add_need(ctx, a, b, "him")["ok"] is True
    rows = ctx.con.execute("select * from teach_needs").fetchall()
    assert len(rows) == 1 and rows[0]["origin"] == "him" and rows[0]["status"] == "active"


def test_a_loop_is_refused_and_the_chain_is_named(ctx, subj):
    a, b, c = (topic(ctx, subj, k) for k in ("alpha", "beta", "gamma"))
    assert tm.add_need(ctx, a, b, "model")["ok"] and tm.add_need(ctx, b, c, "model")["ok"]
    r = tm.add_need(ctx, c, a, "model")
    assert r["ok"] is False and r["chain"] == ["gamma", "alpha", "beta", "gamma"]
    assert "gamma" in r["why"] and "alpha" in r["why"]
    assert ctx.con.execute("select count(*) from teach_needs").fetchone()[0] == 2


def test_a_topic_cannot_need_itself(ctx, subj):
    a = topic(ctx, subj, "alpha")
    assert tm.add_need(ctx, a, a, "model")["ok"] is False


# ---- weights -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind,w", [("right_alone", 1.0), ("right_helped", 0.4), ("partly", 0.1), ("wrong", -1.0),
                                    ("class_lost", -0.6), ("asked_help", -0.3), ("taught", 0.0), ("class_seen", 0.0)])
def test_each_kind_has_its_weight(ctx, subj, kind, w):
    tid = topic(ctx, subj, "t")
    ev(ctx, tid, kind)
    row = ctx.con.execute("select * from teach_evidence").fetchone()
    assert row["weight"] == w and row["kind"] == kind
    assert tm.score(ctx, tid, ctx.clock.now()) == pytest.approx(w)


def test_unknown_kind_or_origin_is_refused(ctx, subj):
    tid = topic(ctx, subj, "t")
    with pytest.raises(Fatal):
        tm.record(ctx, tid, "brilliant", "lesson", "x")
    with pytest.raises(Fatal):
        tm.record(ctx, tid, "wrong", "telepathy", "x")


# ---- fading ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("days,expect", [(0, 1.0), (21, 0.5), (42, 0.25)])
def test_evidence_fades_by_half_every_21_days(ctx, subj, days, expect):
    tid = topic(ctx, subj, "t")
    ev(ctx, tid, "right_alone")
    assert tm.score(ctx, tid, ctx.clock.now() + days * DAY) == pytest.approx(expect)


# ---- levels ------------------------------------------------------------------------------------------------------
def test_unknown_then_seen(ctx, subj):
    tid = topic(ctx, subj, "t")
    now = ctx.clock.now()
    assert tm.level(ctx, tid, now) == "unknown"
    ev(ctx, tid, "taught")
    ev(ctx, tid, "class_seen")
    assert tm.level(ctx, tid, now) == "seen"


def test_shaky_edge_at_099_and_100(ctx, subj):
    # right_alone fades: 1.0 at age 0. 0.99 needs age = 21*log2(1/0.99) days.
    import math
    tid = topic(ctx, subj, "t")
    ev(ctx, tid, "right_alone", ago_days=21 * math.log2(1 / 0.99))
    now = ctx.clock.now()
    assert tm.score(ctx, tid, now) == pytest.approx(0.99)
    assert tm.level(ctx, tid, now) == "shaky"
    t2 = topic(ctx, subj, "t2")
    ev(ctx, t2, "right_alone")
    assert tm.level(ctx, t2, now) == "solid"


def test_wrong_beats_right_and_a_later_right_lifts_it(ctx, subj):
    tid = topic(ctx, subj, "t")
    for d in (6, 5):
        ev(ctx, tid, "right_alone", ago_days=d)
    now = ctx.clock.now()
    assert tm.level(ctx, tid, now) == "solid"
    ev(ctx, tid, "wrong", ago_days=3)
    ev(ctx, tid, "right_alone", ago_days=1)  # wrong is no longer the newest answer
    assert tm.score(ctx, tid, now) > 1.0 and tm.level(ctx, tid, now) == "solid"


def test_a_wrong_after_three_rights_is_shaky_even_with_a_high_score(ctx, subj):
    tid = topic(ctx, subj, "t")
    for d in (6, 5, 4):
        ev(ctx, tid, "right_alone", ago_days=d)
    ev(ctx, tid, "wrong", ago_days=0.01)
    now = ctx.clock.now()
    assert tm.score(ctx, tid, now) > 1.0          # 3 rights still outweigh one wrong
    assert tm.level(ctx, tid, now) == "shaky"


def test_mastered_edge_at_249_and_250(ctx, subj):
    import math
    tid = topic(ctx, subj, "t")
    # three right_alone on three days, all aged the same factor f so the score is 3f
    for d in (0.0, 1.0, 2.0):
        ev(ctx, tid, "right_alone", ago_days=d)
    now = ctx.clock.now()
    s = tm.score(ctx, tid, now)
    assert 2.5 < s < 3.0 and tm.level(ctx, tid, now) == "mastered"
    # push now forward until the score is just under 2.5
    later = now + 21 * math.log2(s / 2.49) * DAY
    assert tm.score(ctx, tid, later) == pytest.approx(2.49) and tm.level(ctx, tid, later) == "solid"
    exact = now + 21 * math.log2(s / 2.5) * DAY - 1
    assert tm.level(ctx, tid, exact) == "mastered"


def test_two_right_alone_on_the_same_day_is_not_mastered(ctx, subj):
    tid = topic(ctx, subj, "t")
    for _ in range(3):
        ev(ctx, tid, "right_alone")
    now = ctx.clock.now()
    assert tm.score(ctx, tid, now) == pytest.approx(3.0)
    assert tm.level(ctx, tid, now) == "solid"
    ev(ctx, tid, "right_alone", ago_days=1)
    assert tm.level(ctx, tid, now) == "mastered"


def test_mastered_left_alone_fades_to_shaky_in_four_to_seven_weeks(ctx, subj):
    tid = topic(ctx, subj, "t")
    ev(ctx, tid, "right_alone", ago_days=1)
    ev(ctx, tid, "right_alone", ago_days=0)
    ev(ctx, tid, "right_alone", ago_days=0)   # score 2.97 -> shaky after about 33 days
    now = ctx.clock.now()
    assert tm.level(ctx, tid, now) == "mastered"
    assert tm.level(ctx, tid, now + 28 * DAY) in ("solid", "shaky")
    assert tm.level(ctx, tid, now + 49 * DAY) == "shaky"


def test_only_negative_class_evidence_is_shaky(ctx, subj):
    tid = topic(ctx, subj, "t")
    ev(ctx, tid, "class_lost")
    assert tm.level(ctx, tid, ctx.clock.now()) == "shaky"


# ---- due ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("streak,days", [(1, 1), (2, 3), (3, 7), (4, 16), (5, 35), (6, 35), (9, 35)])
def test_streak_intervals(ctx, subj, streak, days):
    tid = topic(ctx, subj, "t")
    for i in range(streak):
        ev(ctx, tid, "right_alone", ago_days=(streak - i) * 0.1)
    last = ctx.clock.now() - 0.1 * DAY
    assert tm.due(ctx, tid, ctx.clock.now()) == pytest.approx(last + days * DAY)


def test_a_wrong_resets_the_streak(ctx, subj):
    tid = topic(ctx, subj, "t")
    for d in (5, 4, 3):
        ev(ctx, tid, "right_alone", ago_days=d)
    ev(ctx, tid, "wrong", ago_days=2)
    last = ctx.clock.now() - 2 * DAY
    assert tm.due(ctx, tid, ctx.clock.now()) == pytest.approx(last + 1 * DAY)
    ev(ctx, tid, "right_alone", ago_days=1)
    assert tm.due(ctx, tid, ctx.clock.now()) == pytest.approx(ctx.clock.now() - DAY + 1 * DAY)


def test_only_negative_evidence_and_no_answer_is_due_now_and_unknown_or_seen_never(ctx, subj):
    a, b, c = (topic(ctx, subj, k) for k in ("aa", "bb", "cc"))
    now = ctx.clock.now()
    ev(ctx, a, "class_lost")
    ev(ctx, b, "taught")
    assert tm.due(ctx, a, now) == now
    assert tm.due(ctx, b, now) is None and tm.due(ctx, c, now) is None


# ---- weakest and ready -------------------------------------------------------------------------------------------
def test_weakest_is_shaky_only_lowest_score_first_then_newest_bad(ctx, subj):
    a, b, c, d, e = (topic(ctx, subj, k) for k in ("aa", "bb", "cc", "dd", "ee"))
    ev(ctx, a, "wrong", ago_days=2)                       # -1.0 * fade
    ev(ctx, b, "wrong", ago_days=2); ev(ctx, b, "wrong", ago_days=1)  # lower score
    ev(ctx, c, "right_alone", ago_days=1)
    ev(ctx, c, "right_alone", ago_days=0)                 # solid, not weak
    ev(ctx, d, "taught")                                  # seen, not weak
    # e is unknown, not weak
    got = [w["topic_id"] for w in tm.weakest(ctx, subj, 5, ctx.clock.now())]
    assert got == [b, a]
    assert [w["topic_id"] for w in tm.weakest(ctx, subj, 1, ctx.clock.now())] == [b]


def test_weakest_ties_go_to_the_most_recent_bad_evidence(ctx, subj):
    a, b = topic(ctx, subj, "aa"), topic(ctx, subj, "bb")
    now = ctx.clock.now()
    # equal faded scores on purpose: a's row is 3 days old at weight -1.0, b's is 1 day old at a weight that fades to the same
    for tid, age, w in ((a, 3, -1.0), (b, 1, -(0.5 ** (2 / 21)))):
        ctx.con.execute("insert into teach_evidence(topic_id, at, kind, weight, origin, origin_id, note) "
                        "values (?,?,?,?,?,?,?)", (tid, now - age * DAY, "wrong", w, "lesson", "x", ""))
    sa, sb = (tm.score(ctx, t, now) for t in (a, b))
    assert sa == pytest.approx(sb)
    assert [g["topic_id"] for g in tm.weakest(ctx, subj, 2, now)] == [b, a]


def test_ready_needs_are_all_solid_or_mastered(ctx, subj):
    base, mid, top, lone = (topic(ctx, subj, k) for k in ("base", "mid", "top", "lone"))
    tm.add_need(ctx, mid, base, "model")
    tm.add_need(ctx, top, mid, "model")
    now = ctx.clock.now()
    # nothing known: base (no needs) and lone are ready, mid and top are not
    assert {r["topic_id"] for r in tm.ready(ctx, subj, now)} == {base, lone}
    ev(ctx, base, "right_alone", ago_days=1); ev(ctx, base, "right_alone")
    assert {r["topic_id"] for r in tm.ready(ctx, subj, now)} == {mid, lone}      # base is solid now, so not ready
    ev(ctx, mid, "right_alone", ago_days=1); ev(ctx, mid, "right_alone")
    assert {r["topic_id"] for r in tm.ready(ctx, subj, now)} == {top, lone}


def test_dropped_and_merged_topics_are_ignored(ctx, subj):
    a, b = topic(ctx, subj, "aa"), topic(ctx, subj, "bb")
    ctx.con.execute("update teach_topics set status='dropped' where id=?", (b,))
    assert [r["topic_id"] for r in tm.ready(ctx, subj, ctx.clock.now())] == [a]


# ---- slips table is ready for E12 --------------------------------------------------------------------------------
def test_a_slip_row_can_be_written_with_no_kind(ctx, subj):
    tid = topic(ctx, subj, "t")
    eid = ev(ctx, tid, "wrong")
    ctx.con.execute("insert into teach_slips(id, evidence_id, topic_id, at, question, his_answer, right_answer, kind, timed) "
                    "values ('s1', ?, ?, 1.0, 'q', 'a', 'b', '', 0)", (eid, tid))
    assert ctx.con.execute("select kind from teach_slips").fetchone()[0] == ""


# ---- works for a made-up person ----------------------------------------------------------------------------------
def test_a_made_up_person_with_a_non_school_subject(ctx):
    from personas import PERSONAS
    assert "half marathon" in PERSONAS["nurse"]["about"]
    s = tm.add_subject(ctx, "Half marathon plan", "Half marathon plan")["id"]
    easy = topic(ctx, s, "easy pace")
    long_run = topic(ctx, s, "long run")
    tm.add_need(ctx, long_run, easy, "model")
    now = ctx.clock.now()
    assert [r["topic_id"] for r in tm.ready(ctx, s, now)] == [easy]
    ev(ctx, easy, "wrong")
    assert [w["topic_id"] for w in tm.weakest(ctx, s, 3, now)] == [easy]
    ev(ctx, easy, "right_alone", ago_days=-0.001)  # newest
    ev(ctx, easy, "right_alone", ago_days=-0.002)
    ev(ctx, easy, "right_alone", ago_days=-0.003)
    assert tm.level(ctx, easy, now + 0.01 * DAY) == "solid"
