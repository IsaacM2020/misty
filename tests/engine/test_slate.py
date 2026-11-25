"""S1: the Slate's page format. The answer comes from code, never from the model."""
import pytest

from misty_engine import slate

from test_chat import ScriptBrain, call, say


def mathblock(expr="5*5", tex="5 \\times 5 = \\ans", **kw):
    return {"k": "math", "tex": tex, "ans": {"expr": expr, **kw}, "say": "Five times five is twenty-five."}


def test_the_answer_is_worked_out_by_code():
    s = slate.build({"title": "5 x 5", "blocks": [mathblock()]})
    assert s["blocks"][0]["ans"] == {"expr": "5*5", "value": "25"}
    assert s["kind"] == "quick" and s["blocks"][0]["mark"] == "under"       # the last block always carries the mark


def test_a_wrong_answer_from_the_model_is_replaced():
    s = slate.build({"blocks": [mathblock(value="26")]})
    assert s["blocks"][0]["ans"]["value"] == "25"


@pytest.mark.parametrize("expr,want", [("2**10", "1024"), ("10/4", "2.5"), ("0.1+0.2", "0.3"), ("sqrt(144)", "12"),
                                       ("17% of 340".replace("17% of 340", "0.17*340"), "57.8")])
def test_numbers_are_tidy(expr, want):
    assert slate.build({"blocks": [mathblock(expr)]})["blocks"][0]["ans"]["value"] == want


def test_a_sum_that_does_not_compute_kills_the_slate():
    with pytest.raises(ValueError):
        slate.build({"blocks": [mathblock("5*")]})
    with pytest.raises(ValueError):
        slate.build({"blocks": [mathblock("1/0")]})


def test_a_bad_working_line_is_dropped_but_the_answer_stays():
    s = slate.build({"blocks": [{"k": "math", "tex": ""}, mathblock()]})
    assert len(s["blocks"]) == 1 and s["dropped"]


@pytest.mark.parametrize("raw", [None, "x", {}, {"blocks": []}, {"blocks": [{"k": "text"}]}])
def test_junk_is_refused(raw):
    with pytest.raises(ValueError):
        slate.build(raw)


@pytest.mark.asyncio
async def test_slate_tool_sends_the_checked_slate_to_the_app(ctx):
    sent = []

    async def notify(method, params):
        sent.append((method, params))
    ctx.notify = notify
    ctx.openrouter = ScriptBrain({"calls": [call("slate", slate={"title": "5 x 5", "blocks": [mathblock(value="99")]})]},
                                 {"content": "Twenty-five."})
    out, text, _ = await say(ctx, "what's 5x5")
    page = next(p for m, p in sent if m == "slate.show")
    assert page["blocks"][0]["ans"]["value"] == "25"
    assert out["actions"][0]["tool"] == "slate" and out["actions"][0]["ok"]


@pytest.mark.asyncio
async def test_a_failed_slate_tells_the_model_to_just_answer(ctx):
    sent = []

    async def notify(method, params):
        sent.append(method)
    ctx.notify = notify
    ctx.openrouter = ScriptBrain({"calls": [call("slate", slate={"blocks": [mathblock("5*")]})]}, {"content": "Twenty-five."})
    out, text, _ = await say(ctx, "what's 5x5")
    assert "slate.show" not in sent and not out["actions"][0]["ok"]


# ---- S2: every block kind, the checker, and the fixture slates

import json
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "slates"
GOOD = sorted((FIX / "good").glob("*.json"))
BAD = sorted((FIX / "bad").glob("*.json"))


def test_fixture_counts():
    assert len(GOOD) == 60 and len(BAD) == 10


@pytest.mark.parametrize("path", GOOD, ids=lambda p: p.stem)
def test_good_slates_pass_unchanged(path):
    raw = json.loads(path.read_text())
    s = slate.build(raw)
    assert s["dropped"] == [] and len(s["blocks"]) == len(raw["blocks"])
    assert s["kind"] == raw["_kind"]
    assert any(b.get("mark") for b in s["blocks"])                          # ends on a marked answer
    for b_in, b_out in zip(raw["blocks"], s["blocks"]):
        assert b_in["k"] == b_out["k"]
        for key in ("tex", "text", "items", "prompt"):
            if key in b_in and key != "items" and not slate._TOK.search(b_in[key]):      # \geom etc. are filled in by code
                assert b_out[key] == b_in[key]


@pytest.mark.parametrize("path", BAD, ids=lambda p: p.stem)
def test_bad_slates_are_rejected_or_repaired(path):
    raw = json.loads(path.read_text())
    want = raw["_expect"]
    if want.get("reject"):
        with pytest.raises(ValueError):
            slate.build(raw)
    else:
        s = slate.build(raw)
        assert len(s["dropped"]) == want["dropped"]
        assert s["blocks"][-1].get("mark")


def test_kind_comes_from_code_not_the_model():
    s = slate.build({"kind": "stay", "blocks": [{"k": "text", "text": "Hi"}]})
    assert s["kind"] == "quick"
    s = slate.build({"kind": "quick", "blocks": [{"k": "steps", "items": ["one"]}]})
    assert s["kind"] == "stay"
    s = slate.build({"blocks": [{"k": "text", "text": f"line {i}"} for i in range(4)]})
    assert s["kind"] == "stay"


def test_the_mark_goes_on_the_last_answer_not_on_a_turn():
    s = slate.build({"blocks": [{"k": "text", "text": "Speed is d over t"}, {"k": "turn", "prompt": "Your turn"}]})
    assert s["blocks"][0].get("mark") and "mark" not in s["blocks"][1]


@pytest.mark.parametrize("lhs,rhs,ok", [("2*3", "6", True), ("2*3", "7", False),
                                       ("diff(x**3,x)", "3*x**2", True), ("diff(x**3,x)", "2*x**2", False),
                                       ("sin(x)**2+cos(x)**2", "1", True), ("(x+1)**2", "x**2+2*x+1", True),
                                       ("(x+1)**2", "x**2+1", False), ("integrate(x,(x,0,2))", "2", True)])
def test_check_equal(lhs, rhs, ok):
    assert slate.check_equal(lhs, rhs) is ok


@pytest.mark.parametrize("lhs", ["__import__('os')", "open('x')", "foo(1)", "x;y", "[1]"])
def test_check_refuses_anything_that_is_not_maths(lhs):
    with pytest.raises(ValueError):
        slate.check_equal(lhs, "1")


def test_limits():
    assert slate.words("one $x+y$ three") == 3
    with pytest.raises(ValueError):
        slate.build({"blocks": [{"k": "text", "text": "x"}] * 13})


def test_a_plot_must_be_drawable():
    ok = {"k": "diagram", "items": [{"t": "plot", "f": "x**2"}]}
    bad = {"k": "diagram", "items": [{"t": "plot", "f": "foo(x)"}]}
    assert slate.build({"blocks": [ok]})["dropped"] == []
    with pytest.raises(ValueError, match="diagram"):      # a picture that can't be drawn fails the slate, so she retries
        slate.build({"blocks": [bad, ok]})
    with pytest.raises(ValueError):
        slate.build({"blocks": [bad]})


# ---------------------------------------------------------------- S4: geometry by code
import math
from misty_engine import slate_geo

DIAGRAMS = [p for p in GOOD if any(b["k"] == "diagram" for b in json.loads(p.read_text())["blocks"])]


def _geos(path):
    return [b["geo"] for b in slate.build(json.loads(path.read_text()))["blocks"] if b.get("geo")]


def test_twenty_diagrams_are_drawn():
    assert sum(1 for p in DIAGRAMS if _geos(p)) >= 20


@pytest.mark.parametrize("path", DIAGRAMS, ids=lambda p: p.stem)
def test_no_label_touches_a_stroke_or_another_label(path):
    for g in _geos(path):
        assert slate_geo.overlaps(g) == []
        assert not any(l.get("crowded") for l in g["labels"])


@pytest.mark.parametrize("path", DIAGRAMS, ids=lambda p: p.stem)
def test_closed_shapes_close(path):
    for g in _geos(path):
        for s in g["strokes"]:
            if s["closed"]:
                assert math.dist(s["pts"][0], s["pts"][-1]) < 1e-6


def test_the_unit_circle_is_round_and_the_point_is_on_it():
    g = _geos(FIX / "good" / "unit-circle-pi6.json")[0]
    c = next(s for s in g["strokes"] if s["k"] == "circle")
    xs, ys = [p[0] for p in c["pts"]], [p[1] for p in c["pts"]]
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    r = (max(xs) - min(xs)) / 2
    assert abs(r - (max(ys) - min(ys)) / 2) < 0.05                        # as wide as tall: a circle, not an oval
    assert all(abs(math.dist(p, (cx, cy)) - r) < 0.05 for p in c["pts"])
    d = g["dots"][0]
    assert abs(math.dist((d["x"], d["y"]), (cx, cy)) - r) < 0.05          # the point sits on the circle
    assert abs(math.atan2(cy - d["y"], d["x"] - cx) - math.radians(30)) < 0.002


def test_the_model_still_cannot_send_coordinates():
    with pytest.raises(ValueError):
        slate.build({"title": "x", "kind": "quick", "blocks": [{"k": "diagram", "items": [{"t": "circle", "x": 0, "y": 0, "r": 1, "cx": 5}], "say": "x", "mark": "under"}]})
