"""Regressions from Isaac's first live Slate test (17 Nov 2026, SF4 seesaw): the drawing was dropped and text lines were lost."""
from pathlib import Path

from misty_engine import chat, slate


def test_molecule_under_another_key_still_draws():
    # Haiku sent the molecule without `formula`; the checker dropped it, so no drawing showed.
    for key in ("name", "f", "molecule"):
        out = slate.build({"title": "SF4", "blocks": [{"k": "diagram", "say": "Here is SF4.", "items": [{"t": "molecule", key: "SF4"}]}]})
        assert out.get("dropped") == []
        assert out["blocks"][0]["chem"]["geometry"] == "seesaw"


def test_a_few_words_over_sixteen_is_kept():
    line = "A five-domain system is trigonal bipyramidal: five electron domains around the central atom."
    out = slate.build({"title": "t", "blocks": [{"k": "text", "say": "s", "text": line}]})
    assert out.get("dropped") == [] and out["blocks"][0]["text"] == line


def test_tool_tells_the_model_each_shapes_keys():
    assert "molecule{formula,charge?}" in chat._SLATE_PRIMS


def test_unwritten_words_are_fully_hidden():
    # The mask's hidden end must be fully transparent, or every word shows as a grey ghost before it is inked.
    css = (Path(__file__).parents[2] / "app/Misty/Resources/slate/slate.css").read_text()
    assert "transparent 68.75%,transparent 100%" in css


# --- 17 Nov 2026, second live test: both diagrams were dropped and she still said "drawn".

def test_axes_sent_with_names_instead_of_ranges_still_draw():
    # "Price signalling": axes x="Quantity", plots f="supply"/"demand". It must draw the supply and demand cross.
    out = slate.build({"title": "Price signalling", "blocks": [
        {"k": "text", "say": "s", "text": "Price signalling: a higher price tells producers to supply more."},
        {"k": "diagram", "say": "s", "items": [{"t": "axes", "x": "Quantity", "y": "Price"},
                                               {"t": "plot", "f": "supply", "label": "Supply"},
                                               {"t": "plot", "f": "demand", "label": "Demand"},
                                               {"t": "point", "x": 5, "y": 5, "label": "Equilibrium"}]}]})
    assert out["dropped"] == []
    d = out["blocks"][1]
    assert d["k"] == "diagram" and d["items"][0] == {"t": "curve_pair", "kind": "supply_demand", "labels": ["Supply", "Demand"]}
    assert d.get("geo")


def test_plain_axes_with_names_get_a_range():
    out = slate.build({"title": "t", "blocks": [{"k": "diagram", "say": "s", "items": [
        {"t": "axes", "x": "Time", "y": "Speed"}, {"t": "plot", "f": "2*x"}]}]})
    ax = out["blocks"][0]["items"][0]
    assert out["dropped"] == [] and ax["x"] == [0, 10] and ax["xl"] == "Time" and ax["yl"] == "Speed"


def test_molecule_named_only_in_its_label_still_draws():
    for label in ("CH4: four bonding domains, tetrahedral", "SF4 seesaw: S with four F, one lone pair on top"):
        out = slate.build({"title": "t", "blocks": [{"k": "diagram", "say": "s", "items": [{"t": "molecule", "label": label}]}]})
        assert out["dropped"] == [] and out["blocks"][0]["items"][0]["formula"] in ("CH4", "SF4")


def test_a_long_line_is_split_not_lost():
    line = ("An electron domain is any region of electron density around the central atom: a bonded pair, "
            "a double bond or a lone pair each count as one.")
    out = slate.build({"title": "t", "blocks": [{"k": "text", "say": "s", "text": line}]})
    assert out["dropped"] == [] and len(out["blocks"]) == 2
    assert " ".join(b["text"] for b in out["blocks"]) == line


def test_a_diagram_that_cannot_be_drawn_fails_the_slate_so_she_retries():
    # A dropped diagram used to come back ok, so she told him "drawn" over a page with no picture.
    import pytest
    with pytest.raises(ValueError, match="diagram"):
        slate.build({"title": "t", "blocks": [{"k": "text", "say": "s", "text": "Look."},
                                              {"k": "diagram", "say": "s", "items": [{"t": "plot", "f": "banana(x)"}]}]})


def test_a_line_comparing_shapes_is_not_a_wrong_claim():
    # "four domains give tetrahedral, five give trigonal bipyramidal" under CH4 killed the slate.
    out = slate.build({"title": "t", "blocks": [
        {"k": "diagram", "say": "s", "items": [{"t": "molecule", "formula": "CH4"}]},
        {"k": "text", "say": "s", "text": "Four domains give tetrahedral, five give trigonal bipyramidal."}]})
    assert [b["k"] for b in out["blocks"]] == ["diagram", "text"]
    import pytest
    with pytest.raises(ValueError):             # a plain wrong claim still fails
        slate.build({"title": "t", "blocks": [
            {"k": "diagram", "say": "s", "items": [{"t": "molecule", "formula": "CH4"}]},
            {"k": "text", "say": "s", "text": "CH4 is trigonal bipyramidal."}]})
