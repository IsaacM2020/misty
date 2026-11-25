"""E18: the Learning view's numbers. A hand-checked table on a fixture db, no model call, a made-up stranger's subject."""
import json

import pytest

from misty_engine.clock import sgt_date
from misty_engine.teach import overview
from misty_engine.teach import map as tm

DAY = 86400.0
CITE = {"source_id": "s1", "chunk_no": 0, "quote": "a short quote of at least five words"}


def topic(ctx, sid, key):
    return tm.add_topic(ctx, sid, key, key.title(), CITE)["id"]


def ev(ctx, tid, kind, days_ago=0.0):
    i = tm.record(ctx, tid, kind, "drill")
    ctx.con.execute("UPDATE teach_evidence SET at=? WHERE id=?", (ctx.clock.now() - days_ago * DAY, i))


def day(ctx, n):
    return sgt_date(ctx.clock.now() + n * DAY)


@pytest.fixture
def table(fctx):
    """Six topics with a known level each. Hand-checked:
         a  right alone 3 times on 3 days, long ago-ish      -> mastered
         b  right alone once today                          -> solid
         c  wrong yesterday                                 -> shaky (and due now)
         d  right alone once 60 days ago                    -> shaky (score fell under 1.0), due
         e  nothing                                         -> unknown
         f  skipped by the course                           -> not counted
    """
    s = tm.add_subject(fctx, "baking", "Baking")["id"]
    ids = {k: topic(fctx, s, k) for k in "abcdef"}
    for n in (3, 2, 1):
        ev(fctx, ids["a"], "right_alone", n)
    ev(fctx, ids["b"], "right_alone", 0)
    ev(fctx, ids["c"], "wrong", 1)
    ev(fctx, ids["d"], "right_alone", 60)
    ev(fctx, ids["f"], "right_alone", 0)
    now = fctx.clock.now()
    fctx.con.execute("INSERT INTO teach_courses(id, subject_id, goal, by_date, per_day, status, proposed_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("c1", s, "bake bread", day(fctx, 10), 1.0, "active", json.dumps({"skip": [ids["f"]]}), now, now))
    for d, t, k in ((day(fctx, 0), "e", "new"), (day(fctx, 1), "c", "review")):
        fctx.con.execute("INSERT INTO teach_course_days(course_id, day, topic_id, kind, status) VALUES ('c1',?,?,?, 'planned')", (d, ids[t], k))
    return s, ids


def test_the_levels_are_what_the_table_says(fctx, table):
    s, ids = table
    now = fctx.clock.now()
    assert {k: tm.level(fctx, v, now) for k, v in ids.items()} == {"a": "mastered", "b": "solid", "c": "shaky", "d": "shaky", "e": "unknown", "f": "solid"}


def test_a_course_counts_equal_the_hand_checked_table(fctx, table):
    c = overview.overview(fctx)["courses"][0]
    assert c["subject"] == "Baking" and c["goal"] == "bake bread" and c["status"] == "active"
    assert c["topics"] == 5 and c["solid"] == 2 and c["shaky"] == 2 and c["due"] >= 2
    assert c["days_left"] == 10 and c["by"] == day(fctx, 10)
    assert c["next"] == {"topic": "E", "day": day(fctx, 0), "kind": "new"}


def test_the_shaky_list_has_a_plain_reason_and_the_weakest_first(fctx, table):
    sh = overview.overview(fctx)["shaky"]
    assert [r["topic"] for r in sh] == ["C", "D"]
    assert "you got one wrong 1 day ago" in sh[0]["why"]
    assert all(r["level"] == "shaky" and r["subject"] == "Baking" for r in sh)


def test_due_lists_only_topics_that_were_taught(fctx, table):
    titles = [r["topic"] for r in overview.overview(fctx)["due"]]
    assert "E" not in titles and "C" in titles and "D" in titles


def test_the_next_bite_moves_to_tomorrow_when_today_is_done(fctx, table):
    fctx.con.execute("UPDATE teach_course_days SET status='done' WHERE kind='new'")
    assert overview.overview(fctx)["courses"][0]["next"] == {"topic": "C", "day": day(fctx, 1), "kind": "review"}


def test_a_paused_course_is_listed_with_no_next_bite_and_a_dropped_one_is_not(fctx, table):
    s, ids = table
    fctx.con.execute("UPDATE teach_courses SET status='paused'")
    c = overview.overview(fctx)["courses"][0]
    assert c["status"] == "paused" and c["next"] is None
    fctx.con.execute("UPDATE teach_courses SET status='dropped'")
    o = overview.overview(fctx)
    assert o["courses"] == [] and o["shaky"] == []


def test_a_course_with_no_date_has_no_days_left(fctx, table):
    fctx.con.execute("UPDATE teach_courses SET by_date=NULL")
    assert overview.overview(fctx)["courses"][0]["days_left"] is None


def test_drills_count_only_the_last_seven_days_and_only_finished_ones(fctx, table):
    s, ids = table
    now = fctx.clock.now()
    for i, (kind, status, r, w, ago) in enumerate((("drill", "done", 4, 1, 1), ("review", "done", 2, 0, 3), ("probe", "done", 3, 0, 2),
                                                    ("drill", "done", 5, 5, 9), ("drill", "stopped", 1, 1, 1), ("drill", "live", 0, 0, 0))):
        if status == "live":
            continue
        fctx.con.execute("INSERT INTO teach_drill(id, subject_id, topic_id, kind, status, right, wrong, started_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (f"d{i}", s, ids["a"], kind, status, r, w, now - ago * DAY, now - ago * DAY))
    d = overview.overview(fctx)["drills"]
    assert d["count"] == 3 and d["by_kind"] == {"drill": 1, "review": 1, "probe": 1} and d["right"] == 9 and d["wrong"] == 1


def test_probe_lines_come_from_the_probe_rows(fctx, table):
    s, ids = table
    now = fctx.clock.now()
    for n, (name, score) in enumerate((("before", 1), ("after", 3))):
        fctx.con.execute("INSERT INTO teach_probes(id, topic_id, set_name, qids, score, out_of, lesson_id, at, status) VALUES (?,?,?,?,?,?,?,?,'done')",
                         (f"p{n}", ids["b"], name, "[]", score, 3, "", now - 100 + n))
    assert overview.overview(fctx)["probes"] == [{"topic": "B", "line": "1 of 3 before, 3 of 3 now."}]


def test_the_mistakes_counts(fctx, table):
    s, ids = table
    now = fctx.clock.now()
    rows = [("didnt_know", 0, 0, 1), ("didnt_know", 2, 0, 3), ("slipped", 0, 0, 10), ("", 0, 0, 0), ("misread", 0, 1, 1)]
    for i, (kind, rights, flagged, ago) in enumerate(rows):
        fctx.con.execute("INSERT INTO teach_slips(id, topic_id, at, question, kind, rights, flagged) VALUES (?,?,?,?,?,?,?)",
                         (f"sl{i}", ids["c"], now - ago * DAY, f"q{i}", kind, rights, flagged))
    m = overview.overview(fctx)["mistakes"]
    assert m["total"] == 4 and m["week"] == 3 and m["retired"] == 1 and m["flagged"] == 1
    assert m["by_kind"] == {"didnt_know": 2, "slipped": 1, "not filed yet": 1} and m["pattern"] == ""


def test_a_pattern_is_the_same_sentence_the_mistakes_book_says(fctx, table):
    s, ids = table
    now = fctx.clock.now()
    for i in range(8):
        fctx.con.execute("INSERT INTO teach_slips(id, topic_id, at, question, kind) VALUES (?,?,?,?,?)", (f"sl{i}", ids["c"], now - i * 3600, f"q{i}", "slipped"))
    assert overview.overview(fctx)["mistakes"]["pattern"] == "In Baking, 8 of your last 8 mistakes were small slips on things you know."


def test_a_person_with_nothing_learned_gets_empty_lists_and_zeros(fctx):
    o = overview.overview(fctx)
    assert o["courses"] == [] and o["shaky"] == [] and o["due"] == [] and o["probes"] == []
    assert o["drills"]["count"] == 0 and o["mistakes"]["total"] == 0 and o["mistakes"]["pattern"] == ""


def test_the_view_changes_nothing_and_never_asks_a_model(fctx, table, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("the overview must not call a model")
    monkeypatch.setattr(fctx.openrouter, "post", boom)
    n = fctx.con.total_changes
    overview.overview(fctx)
    assert fctx.con.total_changes == n


@pytest.mark.asyncio
async def test_the_rpc_is_registered_and_answers_the_same(fctx, table):
    from misty_engine.api import build_methods
    got = await build_methods(fctx)["teach.overview"]({})
    assert got == overview.overview(fctx) and got["courses"][0]["subject"] == "Baking"
