"""G1: scripts/checks/catch_labels.py logic, run free with a fake judge (no network, no money)."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "checks"))
import catch_labels as cl  # noqa: E402
from catchset import load  # noqa: E402


def by_id(i):
    return next(c for c in load() if c.id == i)


def test_the_blind_prompt_has_the_page_text_and_no_label():
    c = by_id("m01")
    p = cl.blind_prompt(c)
    assert "5x = 32 + 7" in p and "Untitled" in p or "Share" in p
    assert "sign" not in p.lower().split("page text")[0] and c.note not in p


def test_parse_reads_json_and_falls_back_to_no_error_on_junk():
    assert cl.parse('{"error": true, "line": 2, "kind": "sign"}') == {"error": True, "line": 2, "kind": "sign"}
    assert cl.parse("blah") == {"error": False, "line": 0, "kind": ""}
    assert cl.parse('{"error": false}')["error"] is False


def test_agree_needs_the_same_line_and_a_clean_file_clean():
    err, ok = by_id("m01"), by_id("m07")
    assert cl.agrees(err, {"error": True, "line": 2, "kind": "sign"})
    assert cl.agrees(err, {"error": True, "line": 2, "kind": "arithmetic"})        # the line is what counts, the kind is noted only
    assert not cl.agrees(err, {"error": True, "line": 3, "kind": "sign"})
    assert not cl.agrees(err, {"error": False, "line": 0, "kind": ""})
    assert cl.agrees(ok, {"error": False, "line": 0, "kind": ""})
    assert not cl.agrees(ok, {"error": True, "line": 2, "kind": "sign"})


def test_run_lists_only_the_disagreements_and_totals_the_money():
    cases = load()

    async def judge(case):
        if case.id == "m01":
            return {"error": False, "line": 0, "kind": ""}, 0.002          # misses its error
        if case.id == "m07":
            return {"error": True, "line": 1, "kind": "sign"}, 0.002          # invents one
        return ({"error": True, "line": case.expect["line"], "kind": case.expect["kind"]} if case.expect else {"error": False, "line": 0, "kind": ""}), 0.001

    out = asyncio.run(cl.run(cases, judge))
    assert [d["id"] for d in out["disagree"]] == ["m01", "m07"]
    assert out["n"] == 50 and abs(out["usd"] - (48 * 0.001 + 0.004)) < 1e-9
    assert "missed" in out["disagree"][0]["what"] and "invented" in out["disagree"][1]["what"]
