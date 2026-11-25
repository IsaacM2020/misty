"""E19: the three-weeks scorecard (scripts/checks/teach_report.py). Free, reads tables only, writes nothing to the db."""
import json
import sys
from pathlib import Path

import pytest

from misty_engine.teach import map as tm

DAY = 86400
T0 = 1_800_000_000.0                         # a fixed "now" so every day is known


def _rep():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "checks"))
    import teach_report
    return teach_report


def topic(ctx, key="a"):
    s = tm.add_subject(ctx, "s" + key, "S")["id"]
    return tm.add_topic(ctx, s, key, key.title(), {"source_id": "x", "chunk_no": 0, "quote": key})["id"]


def lesson(ctx, tid, i, status="done", at=T0):
    ctx.con.execute("INSERT OR IGNORE INTO teach_plans(id, topic_id, version, made_at) VALUES ('p', ?, 1, ?)", (tid, at))
    ctx.con.execute("INSERT INTO teach_live(id, topic_id, plan_id, status, started_at, updated_at) VALUES (?,?,?,?,?,?)", (f"l{i}", tid, "p", status, at, at))


def drill(ctx, tid, i, status="done", kind="drill", at=T0):
    ctx.con.execute("INSERT INTO teach_drill(id, topic_id, kind, status, started_at, updated_at) VALUES (?,?,?,?,?,?)", (f"d{i}", tid, kind, status, at, at))


def bite(ctx, key, at, kind="lesson", outcome="told"):
    ctx.con.execute("INSERT INTO teach_bite(key, at, kind, outcome) VALUES (?,?,?,?)", (key, at, kind, outcome))


def build(ctx, **kw):
    kw.setdefault("now", T0 + 21 * DAY)
    return _rep().build(ctx.con, **kw)


def bar(rep, name):
    return next((s, why) for n, s, why in rep["bars"] if n.startswith(name))


def test_empty_db_is_all_waiting_not_fail(fctx):
    rep = build(fctx)
    assert rep["lessons"] == 0 and rep["drills"] == [] and rep["papers"] == 0
    for n in ("learning: after", "learning: a week", "fit: a course", "fit: at most", "his call", "trust: every"):
        assert bar(rep, n)[0] == "WAITING", n
    assert bar(rep, "trust: wrong keys")[0] == "PASS"            # nothing wrong found is a real 0
    assert bar(rep, "spend")[0] == "PASS"
    assert bar(rep, "used enough")[0] == "FAIL"
    assert _rep().render(rep)                                    # prints without a crash on nothing


def test_counts_only_finished_lessons_drills_and_papers(fctx):
    t = topic(fctx)
    for i in range(8):
        lesson(fctx, t, i)
    lesson(fctx, t, 99, status="paused")
    for i in range(10):
        drill(fctx, t, i)
    drill(fctx, t, 98, status="stopped")
    drill(fctx, t, 97, kind="probe")                             # a probe is E17's, not a drill
    fctx.con.execute("INSERT INTO teach_papers(id, subject_id, paper_source_id, scheme_source_id, made_at) VALUES ('pp', ?, 'a', 'b', 0)", (fctx.con.execute("SELECT id FROM teach_subjects").fetchone()[0],))
    fctx.con.execute("INSERT INTO teach_paper_runs(id, paper_id, status, minutes, started_at, ends_at) VALUES ('r1','pp','done',30,?,?)", (T0, T0))
    rep = build(fctx)
    assert (rep["lessons"], len(rep["drills"]), rep["papers"]) == (8, 10, 1)
    assert bar(rep, "used enough")[0] == "PASS"
    assert rep["days"] == 21


def test_one_short_of_each_bar_fails(fctx):
    t = topic(fctx)
    for i in range(7):
        lesson(fctx, t, i)
    for i in range(10):
        drill(fctx, t, i)
    assert bar(build(fctx), "used enough")[0] == "FAIL"


def test_drill_ticks_pass_at_8_of_10_and_fail_when_it_cannot(fctx):
    t = topic(fctx)
    ids = []
    for i in range(10):
        drill(fctx, t, i)
        ids.append(f"d{i}")
    ticks = lambda hits, n: {"drills": {ids[i]: i < hits for i in range(n)}, "his_call": None, "wrong_keys_told": []}
    assert bar(build(fctx, ticks=ticks(8, 10)), "fit: drills")[0] == "PASS"
    assert bar(build(fctx, ticks=ticks(7, 10)), "fit: drills")[0] == "FAIL"
    assert bar(build(fctx, ticks=ticks(5, 5)), "fit: drills")[0] == "WAITING"      # 5 ticked, all hit: still can pass
    assert bar(build(fctx, ticks=ticks(0, 3)), "fit: drills")[0] == "FAIL"         # 3 misses of 10 can no longer reach 8
    assert bar(build(fctx, ticks=ticks(0, 0)), "fit: drills")[0] == "WAITING"


def test_wrong_keys_count_flags_plus_what_he_told(fctx):
    t = topic(fctx)
    fctx.con.execute("INSERT INTO teach_slips(id, topic_id, at, flagged) VALUES ('s1', ?, ?, 1)", (t, T0))
    fctx.con.execute("INSERT INTO teach_slips(id, topic_id, at, flagged) VALUES ('s2', ?, ?, 0)", (t, T0))
    rep = build(fctx)
    assert rep["trust"]["wrong"] == 1 and bar(rep, "trust: wrong keys")[0] == "FAIL"
    told = {"drills": {}, "his_call": None, "wrong_keys_told": ["the cell wall one"]}
    assert build(fctx, ticks=told)["trust"]["wrong"] == 2


def _plan(cite_ok=True):
    c = {"source_id": "src", "chunk_no": 0, "quote": "a real quote here"} if cite_ok else None
    item = {"q": "q?", "key": {"accept": ["x"]}, "hints": [], "cite": c}
    return {"steps": [{"idea": "i", "explain": [{"way": "plain", "say": "s", "cite": c}, {"way": "picture", "say": "s", "cite": c}], "check": item}],
            "guided": item, "solo": {"q": "q?", "key": {"accept": ["x"]}, "cite": c}}


def put_plan(ctx, tid, plan, version=1, status="usable"):
    ctx.con.execute("INSERT INTO teach_plans(id, topic_id, version, plan_json, status, made_at) VALUES (?,?,?,?,?,?)",
                    (f"tp{tid}{version}", tid, version, json.dumps(plan), status, T0))


def test_cites_shape_and_text_check(fctx):
    t = topic(fctx)
    put_plan(fctx, t, _plan())
    c = build(fctx)["cites"]
    assert (c["total"], c["missing"], c["bad"]) == (5, 0, 0)       # 2 spoken, check, guided, solo
    assert build(fctx, checker=lambda s, n, q: False)["cites"]["bad"] == 5
    assert bar(build(fctx, checker=lambda s, n, q: True), "trust: every")[0] == "PASS"
    put_plan(fctx, t, _plan(cite_ok=False), version=2)             # the newer usable plan is the one in use
    c = build(fctx)["cites"]
    assert c["missing"] == 5 and bar(build(fctx), "trust: every")[0] == "FAIL"


def test_a_draft_plan_is_not_counted(fctx):
    t = topic(fctx)
    put_plan(fctx, t, _plan(cite_ok=False), status="draft")
    assert build(fctx)["cites"]["total"] == 0


def test_nags_count_per_singapore_day_and_ignore_dropped_and_match(fctx):
    h = 3600
    bite(fctx, "a", T0, outcome="accepted")
    bite(fctx, "b", T0 + 2 * h, kind="drill", outcome="skipped")                    # same day, a second nag
    bite(fctx, "c", T0 + 3 * h, kind="drill", outcome="dropped")                    # never reached him
    bite(fctx, "d", T0 + 4 * h, kind="match", outcome="topic1")                     # a silent class match
    bite(fctx, "e", T0 + 2 * DAY, outcome="later")
    bite(fctx, "f", T0 + 2 * DAY + 10 * h, kind="eve", outcome="told")              # the one allowed re-ask
    n = build(fctx)["nags"]
    assert n["max"] == 2 and len(n["over"]) == 1 and n["eve"] == 1 and n["days"] == 2
    assert bar(build(fctx), "fit: at most")[0] == "FAIL"


def test_one_nag_a_day_passes(fctx):
    for d in range(5):
        bite(fctx, f"k{d}", T0 + d * DAY)
    assert bar(build(fctx), "fit: at most")[0] == "PASS"


def test_course_span_and_status(fctx):
    s = tm.add_subject(fctx, "z", "Z")["id"]
    fctx.con.execute("INSERT INTO teach_courses(id, subject_id, goal, status, created_at, updated_at) VALUES ('c1',?,?,?,?,?)", (s, "pass it", "active", T0, T0))
    fctx.con.execute("INSERT INTO teach_courses(id, subject_id, goal, status, created_at, updated_at) VALUES ('c2',?,?,?,?,?)", (s, "gone", "dropped", T0 - 99 * DAY, T0))
    assert bar(build(fctx), "fit: a course")[0] == "PASS"
    assert bar(build(fctx, now=T0 + 20 * DAY), "fit: a course")[0] == "FAIL"        # a dropped course never counts


def test_spend_uses_teach_purpose_and_the_daily_cap(fctx):
    ins = lambda ts, usd, p: fctx.con.execute("INSERT INTO spend(ts, provider, model, usd, purpose) VALUES (?,?,?,?,?)", (ts, "or", "m", usd, p))
    ins(T0, 0.04, "teach")
    ins(T0 + 60, 0.05, "teach")
    ins(T0, 9.0, "judge")                                                            # not teaching
    s = build(fctx)["spend"]
    assert s["total"] == 0.09 and s["days"] == 1 and s["over"] == []
    ins(T0 + DAY, 0.11, "teach")
    rep = build(fctx)
    assert len(rep["spend"]["over"]) == 1 and bar(rep, "spend")[0] == "FAIL"
    assert bar(build(fctx, cap=0.2), "spend")[0] == "PASS"


def test_learning_bars_use_the_probe_numbers(fctx):
    t = topic(fctx)
    probe = lambda name, score: fctx.con.execute("INSERT INTO teach_probes(id, topic_id, set_name, qids, score, out_of, lesson_id, at, status) VALUES (?,?,?,?,?,?,?,?,?)",
                                                 (f"pr{name}", t, name, "[]", score, 3, "", T0, "done"))
    probe("before", 1)
    probe("after", 3)
    rep = build(fctx)
    assert bar(rep, "learning: after")[0] == "PASS" and bar(rep, "learning: a week")[0] == "WAITING"
    probe("week", 1)
    assert bar(build(fctx), "learning: a week")[0] == "FAIL"


def test_his_call_and_ticks_file_round_trip(tmp_path):
    r = _rep()
    db = tmp_path / "misty.db"
    assert r.load_ticks(db) == {"drills": {}, "his_call": None, "wrong_keys_told": []}
    r.save_ticks(db, {"drills": {"d1": True, "d2": False}, "his_call": "yes", "wrong_keys_told": ["x"]})
    t = r.load_ticks(db)
    assert t["drills"] == {"d1": True, "d2": False} and t["his_call"] == "yes" and t["wrong_keys_told"] == ["x"]
    (tmp_path / "teach_ticks.json").write_text("not json")                       # a broken file is an empty one, never a crash
    assert r.load_ticks(db)["his_call"] is None


def test_report_does_not_write_the_db(fctx):
    before = fctx.con.total_changes
    build(fctx)
    assert fctx.con.total_changes == before


def test_his_call_bar(fctx):
    mk = lambda v: {"drills": {}, "his_call": v, "wrong_keys_told": []}
    assert bar(build(fctx, ticks=mk("yes")), "his call")[0] == "PASS"
    assert bar(build(fctx, ticks=mk("no")), "his call")[0] == "FAIL"
    assert bar(build(fctx, ticks=mk(None)), "his call")[0] == "WAITING"


def test_script_has_no_model_call():
    src = (Path(__file__).resolve().parents[2] / "scripts" / "checks" / "teach_report.py").read_text()
    for bad in ("ask.think", "openrouter", ".post("):
        assert bad not in src
