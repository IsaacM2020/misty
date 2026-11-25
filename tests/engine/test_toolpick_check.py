"""The tool-pick check's own logic (no model calls): dedupe, lists of answers, harm, tags, live split, the seed, the report."""
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "checks"))
import toolpick as tp  # noqa: E402


def row(say, tool="calc", **kw):
    return {"say": say, "expect_tool": tool, "_src": "t.yaml", **kw}


def test_same_say_and_same_tool_counts_once():
    rows = [row("a"), row("a"), row("a", "search"), row("b")]
    out = tp.dedupe(rows)
    assert [(r["say"], r["expect_tool"]) for r in out] == [("a", "calc"), ("a", "search"), ("b", "calc")]


def test_list_of_answers_and_dedupe_ignores_order():
    assert tp.right(row("x", ["do", "move"]), "move", {})
    assert tp.right(row("x", ["do", "move"]), "do", {})
    assert not tp.right(row("x", ["do", "move"]), "look", {})
    assert len(tp.dedupe([row("x", ["do", "move"]), row("x", ["move", "do"])])) == 1


def test_args_contains_must_match_the_right_answer():
    r = row("call me boss", "way", expect_args_contains="boss")
    assert tp.right(r, "way", {"text": "Call him BOSS"})
    assert not tp.right(r, "way", {"text": "be drier"})


def test_harm_is_an_unwanted_write_call():
    assert tp.harmful(row("x", "recall"), "remember")          # wrote when she should only read
    assert tp.harmful(row("x", "none"), "follow")
    assert not tp.harmful(row("x", "recall"), "search")        # wrong but a read
    assert not tp.harmful(row("x", ["do", "move"]), "move")    # a wanted answer is not a miss at all
    assert not tp.harmful(row("x", "way"), "none")


def test_tool_tables_cover_every_tool():
    from misty_engine import chat
    names = {t["function"]["name"] for t in chat.TOOLS}
    assert (tp.WRITE | tp.READ) >= names, names - (tp.WRITE | tp.READ)
    assert not tp.WRITE & tp.READ


def test_load_tags_split_live_and_dedupe(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("# B2: ways\n- {say: 'one', expect_tool: way}\n- {say: 'two', expect_tool: way, live: true}\n"
                 "# Step C4: screen\n- {say: 'three', expect_tool: look}\n- {say: 'three', expect_tool: look}\n"
                 "- {say: 'four', expect_tool: look, tag: custom}\n")
    rows = tp.load([f])
    assert [r["say"] for r in rows] == ["one", "two", "three", "four"]
    assert [r["_tags"] for r in rows] == [{"b2"}, {"b2"}, {"c4"}, {"custom", "c4"}]
    main, live = tp.split_live(rows)
    assert [r["say"] for r in main] == ["one", "three", "four"] and [r["say"] for r in live] == ["two"]
    assert [r["say"] for r in tp.only(rows, "c4")] == ["three", "four"]
    assert [r["say"] for r in tp.only(rows, "B2")] == ["one", "two"]


def test_state_lines_are_no_longer_skipped(tmp_path):
    f = tmp_path / tp.SAMPLE.name
    f.write_text("- {say: 'stop watching chess', expect_tool: follow, state: true}\n")
    assert len(tp.load([f])) == 1


def test_score_mean_groups_and_harm():
    rows = [row("always", "recall"), row("flaky", "calc"), row("fine", "calc"), row("hurt", "recall")]
    runs = [
        [("search", {}), ("calc", {}), ("calc", {}), ("remember", {})],
        [("search", {}), ("look", {}), ("calc", {}), ("remember", {})],
        [("search", {}), ("calc", {}), ("calc", {}), ("remember", {})],
    ]
    s = tp.score(rows, runs)
    assert s["per_run"] == [2, 1, 2] and s["total"] == 4
    assert s["mean"] == pytest.approx(5 / 3)
    assert [m["say"] for m in s["always"]] == ["always", "hurt"]
    assert [(m["say"], m["missed"]) for m in s["flaky"]] == [("flaky", 1)]
    assert [m["say"] for m in s["harm_always"]] == ["hurt"]
    assert s["harm_count"] == 3                               # 3 harmful calls over the 3 runs
    assert s["harm_lines"] == 1                               # on one line


def test_report_has_the_numbers_and_fractions():
    rows = [row("always", "recall"), row("flaky", "calc")]
    runs = [[("search", {}), ("calc", {})], [("search", {}), ("look", {})], [("search", {}), ("calc", {})]]
    text = tp.report(tp.score(rows, runs), usd=0.0123, runs=3)
    assert "mean 0.67 of 2" in text
    assert "3/3" in text and "1/3" in text
    assert text.index("always wrong") < text.index("flaky")


def test_pass_needs_the_mean_and_no_always_harmful_line():
    ok = tp.score([row("a", "calc")], [[("calc", {})]] * 3)
    assert tp.passed(ok, 0.95)
    bad = tp.score([row("a", "recall")], [[("remember", {})]] * 3)
    assert not tp.passed(bad, 0.0)                              # harmful in all 3 runs fails even at min 0


def test_state_file_seeds_a_fresh_db():
    import _harness
    ctx, home = _harness.temp_ctx(copy_live=False)
    try:
        tp.seed(ctx, tp.STATE)
        spec = yaml.safe_load(tp.STATE.read_text())
        con = ctx.con
        assert con.execute("SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0] == len(spec["facts"])
        assert con.execute("SELECT COUNT(*) FROM follows WHERE status='active'").fetchone()[0] == len(spec["follows"])
        assert con.execute("SELECT COUNT(*) FROM cares WHERE status='active'").fetchone()[0] == len(spec["cares"])
        assert con.execute("SELECT COUNT(*) FROM ways WHERE key='address' AND status='active'").fetchone()[0] == 1   # the engine adds 3 default ways itself
        assert con.execute("SELECT COUNT(*) FROM told").fetchone()[0] == len(spec["told"])
        assert ctx.settings.get("user_name") == "Sam"
        from misty_engine import chat
        text = " ".join(str(m.get("content")) for m in chat.messages(ctx, "hello"))
        assert "bee hive" in text.lower() and "Isaac" not in text
    finally:
        _harness.cleanup(home)
