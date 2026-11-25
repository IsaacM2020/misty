"""G0: scripts/checks/catch_where.py. The ranking reads only app, bundle and minutes. The probe never keeps or prints the text."""
import asyncio
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "checks"))
import catch_where as cw  # noqa: E402

NOW = 1_000_000_000.0
DAY = 86400


def db(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE activity (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, until REAL, bundle TEXT, app TEXT, title TEXT DEFAULT '', doc TEXT DEFAULT '')")
    for at, mins, bundle, app in rows:
        con.execute("INSERT INTO activity(at, until, bundle, app, title) VALUES (?,?,?,?,?)", (at, at + mins * 60, bundle, app, "SECRET TITLE"))
    return con


def test_ranking_sorts_by_minutes_and_sums_stretches():
    con = db([(NOW - DAY, 30, "com.apple.Safari", "Safari"), (NOW - 2 * DAY, 40, "com.apple.Safari", "Safari"), (NOW - DAY, 50, "com.google.Chrome", "Google Chrome")])
    r = cw.rank(con, now=NOW)
    assert [x["app"] for x in r["apps"]] == ["Safari", "Google Chrome"]
    assert r["apps"][0]["minutes"] == 70 and r["apps"][1]["minutes"] == 50
    assert r["apps"][0]["reads_text"] == ""


def test_old_rows_are_outside_the_14_days_and_a_stretch_on_the_edge_is_cut():
    con = db([(NOW - 20 * DAY, 100, "a", "Old"), (NOW - 14 * DAY - 600, 20, "b", "Edge")])
    r = cw.rank(con, now=NOW)
    apps = {x["app"]: x["minutes"] for x in r["apps"]}
    assert "Old" not in apps and apps["Edge"] == 10


def test_top_is_eight_and_the_hours_say_how_thin_the_data_is():
    con = db([(NOW - DAY, i + 1, f"b{i}", f"App{i}") for i in range(12)])
    r = cw.rank(con, now=NOW)
    assert len(r["apps"]) == 8 and r["apps"][0]["app"] == "App11"
    assert r["total_minutes"] == sum(range(1, 13)) and r["days_with_data"] == 1


def test_a_future_stretch_and_an_empty_trail_do_not_break_it():
    assert cw.rank(db([]), now=NOW)["apps"] == []
    con = db([(NOW + 5 * DAY, 60, "a", "Future")])
    assert cw.rank(con, now=NOW)["apps"] == []


def test_render_has_no_titles_and_an_empty_reads_text_column():
    con = db([(NOW - DAY, 30, "com.apple.Safari", "Safari")])
    out = cw.render(cw.rank(con, now=NOW))
    assert "Safari" in out and "SECRET TITLE" not in out and "reads text?" in out


# ---- the probe
def txt(*lines):
    return "\n".join(lines)


def test_analyse_finds_the_marker_and_counts_furniture():
    one = {"text": txt("Untitled document", "Share", "Q1 find x", "3x+2=11"), "how": "accessibility"}
    two = {"text": txt("Untitled document", "Share", "Q1 find x", "3x+2=11", "PROBE 123"), "how": "accessibility"}
    a = cw.analyse(one, two, "PROBE 123")
    assert a["marker"] is True and a["lines"] == 5 and a["furniture"] == 4 and a["how"] == "accessibility"
    assert "text" not in a and "Untitled" not in str(a)


def test_grade_well_thin_cannot():
    well = {"marker": True, "chars": 900, "lines": 20, "furniture": 5, "how": "accessibility"}
    assert cw.grade(well) == "reads well"
    assert cw.grade({**well, "furniture": 8}) == "reads thin"        # 8 of 20 is 40%: too much furniture
    assert cw.grade({**well, "furniture": 6}) == "reads well"        # exactly 30% passes
    assert cw.grade({**well, "furniture": 7}) == "reads thin"
    assert cw.grade({**well, "how": "ocr"}) == "reads thin"
    assert cw.grade({**well, "marker": False}) == "cannot read"
    assert cw.grade({**well, "chars": 0, "lines": 0, "marker": False}) == "cannot read"


def test_a_private_app_is_never_read_or_graded_well():
    a = cw.analyse({"private": True}, {"private": True}, "PROBE 1")
    assert a["private"] is True and cw.grade(a) == "private"


def test_probe_reads_twice_waits_between_and_never_returns_the_text():
    reads = iter([{"text": "a\nb", "how": "accessibility"}, {"text": "a\nb\nPROBE 7", "how": "accessibility"}])
    waits = []

    async def read():
        return next(reads)

    async def sleep(s):
        waits.append(s)

    res = asyncio.run(cw.probe(read, sleep, "PROBE 7", gap=10))
    assert waits == [10] and res["marker"] is True and res["lines"] == 3
    assert "PROBE" not in str(res.get("text", ""))


def test_a_read_that_fails_is_cannot_read_not_a_crash():
    async def read():
        raise RuntimeError("NOT_TRUSTED")

    async def sleep(s):
        return None

    res = asyncio.run(cw.probe(read, sleep, "PROBE 7", gap=0))
    assert res["error"] == "NOT_TRUSTED" and cw.grade(res) == "cannot read"


def test_the_marker_is_fresh_each_time_and_looks_like_probe_digits():
    ms = {cw.new_marker() for _ in range(30)}
    assert len(ms) > 5 and all(m.startswith("PROBE ") and m[6:].isdigit() for m in ms)
