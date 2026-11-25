"""S5: a Lewis structure from a formula. Everything here comes from code, checked against a hand-written answer table."""
import pytest

from misty_engine import chem, slate, slate_geo

# formula, charge: bonds as (atom, atom, order), lone pairs per element, formal charges, geometry
HAND = [
    ("H2O", 0, [("H", "O", 1)] * 2, {"O": [2]}, [], "bent"),
    ("CO2", 0, [("C", "O", 2)] * 2, {"O": [2, 2]}, [], "linear"),
    ("CH4", 0, [("C", "H", 1)] * 4, {}, [], "tetrahedral"),
    ("NH3", 0, [("H", "N", 1)] * 3, {"N": [1]}, [], "trigonal pyramidal"),
    ("O2", 0, [("O", "O", 2)], {"O": [2, 2]}, [], "linear"),
    ("N2", 0, [("N", "N", 3)], {"N": [1, 1]}, [], "linear"),
    ("HCN", 0, [("C", "H", 1), ("C", "N", 3)], {"N": [1]}, [], "linear"),
    ("CH2O", 0, [("C", "H", 1)] * 2 + [("C", "O", 2)], {"O": [2]}, [], "trigonal planar"),
    ("NH4", 1, [("H", "N", 1)] * 4, {}, [1], "tetrahedral"),
    ("OH", -1, [("H", "O", 1)], {"O": [3]}, [-1], "linear"),
    ("CO3", -2, [("C", "O", 1)] * 2 + [("C", "O", 2)], {"O": [2, 3, 3]}, [-1, -1], "trigonal planar"),
    ("SO2", 0, [("O", "S", 2)] * 2, {"O": [2, 2], "S": [1]}, [], "bent"),
    ("BF3", 0, [("B", "F", 1)] * 3, {"F": [3, 3, 3]}, [], "trigonal planar"),
    ("PCl5", 0, [("Cl", "P", 1)] * 5, {"Cl": [3] * 5}, [], "trigonal bipyramidal"),
    ("C2H4", 0, [("C", "C", 2)] + [("C", "H", 1)] * 4, {}, [], "trigonal planar"),
]


def summary(info):
    bonds = sorted(tuple(sorted((info["atoms"][b["a"]]["el"], info["atoms"][b["b"]]["el"]))) + (b["n"],) for b in info["bonds"])
    lps: dict = {}
    for a in info["atoms"]:
        if a["lp"]:
            lps.setdefault(a["el"], []).append(a["lp"])
    return bonds, {k: sorted(v) for k, v in lps.items()}, sorted(a["fc"] for a in info["atoms"] if a["fc"])


@pytest.mark.parametrize("formula,charge,bonds,lps,fcs,geom", HAND, ids=[h[0] for h in HAND])
def test_the_fifteen_molecules(formula, charge, bonds, lps, fcs, geom):
    info = chem.lewis(formula, charge)
    got_b, got_l, got_f = summary(info)
    assert got_b == sorted(tuple(sorted(b[:2])) + (b[2],) for b in bonds)
    assert got_l == {k: sorted(v) for k, v in lps.items()}
    assert got_f == sorted(fcs)
    assert info["geometry"] == geom


def test_the_electron_count_is_right():
    for f, q, ve in [("H2O", 0, 8), ("CO2", 0, 16), ("NH4", 1, 8), ("CO3", -2, 24), ("PCl5", 0, 40), ("OH", -1, 8), ("C2H4", 0, 12)]:
        assert chem.lewis(f, q)["ve"] == ve


EXTRA = ["HF", "CO", "NO", "O3", "SO3", "XeF4", "SF6", "ClF3", "H2O2", "N2H4", "C2H2", "NO3", "CN", "NO2", "H3O", "PO4", "CCl4", "IF5",
         "XeF2", "H2S", "BH3", "CS2", "SCl2", "NF3", "PH3", "SiH4", "COCl2", "ClO2", "N2O4"]


@pytest.mark.parametrize("f", EXTRA)
def test_every_structure_adds_up(f):
    """Whatever the molecule: electrons are conserved, formal charges add up to the charge, no octet breaks."""
    q = {"NO3": -1, "CN": -1, "H3O": 1, "PO4": -3}.get(f, 0)
    try:
        info = chem.lewis(f, q)
    except ValueError:
        return                                                          # refusing is allowed; a wrong picture is not
    assert sum(a["fc"] for a in info["atoms"]) == q
    assert sum(2 * b["n"] for b in info["bonds"]) + sum(2 * a["lp"] + a["rad"] for a in info["atoms"]) == info["ve"]
    for a in info["atoms"]:
        if a["el"] == "H":
            assert a["shell"] == 2
        elif a["el"] in ("C", "N", "O", "F"):
            assert a["shell"] <= 8


@pytest.mark.parametrize("formula,charge,want", [
    ("NO", 0, [("N", 1, 1), ("O", 2, 0)]), ("CO", 0, [("C", 1, 0), ("O", 1, 0)]), ("HF", 0, [("F", 3, 0), ("H", 0, 0)]),
    ("SO3", 0, [("O", 2, 0)] * 3 + [("S", 0, 0)]), ("SF6", 0, [("F", 3, 0)] * 6 + [("S", 0, 0)]), ("XeF4", 0, [("F", 3, 0)] * 4 + [("Xe", 2, 0)])])
def test_the_usual_exceptions(formula, charge, want):
    info = chem.lewis(formula, charge)
    assert sorted((a["el"], a["lp"], a["rad"]) for a in info["atoms"]) == sorted(want)


def test_formal_charge_picks_the_structure():
    assert [b["n"] for b in chem.lewis("BF3")["bonds"]] == [1, 1, 1]       # not B=F: that would put +1 on F
    assert sorted(b["n"] for b in chem.lewis("SO2")["bonds"]) == [2, 2]    # both S=O: every formal charge is zero
    co = chem.lewis("CO")
    assert sorted(a["fc"] for a in co["atoms"]) == [-1, 1] and co["bonds"][0]["n"] == 3
    oz = chem.lewis("O3")
    assert oz["resonance"] and sorted(b["n"] for b in oz["bonds"]) == [1, 2]


@pytest.mark.parametrize("f,geom,domains", [
    ("H2O", "bent", "tetrahedral"), ("NH3", "trigonal pyramidal", "tetrahedral"), ("SO2", "bent", "trigonal planar"),
    ("ClF3", "t-shaped", "trigonal bipyramidal"), ("XeF2", "linear", "trigonal bipyramidal"), ("XeF4", "square planar", "octahedral"),
    ("IF5", "square pyramidal", "octahedral"), ("SF6", "octahedral", "octahedral"), ("CO2", "linear", "linear")])
def test_geometry_names(f, geom, domains):
    info = chem.lewis(f)
    assert (info["geometry"], info["domains"]) == (geom, domains)


@pytest.mark.parametrize("f", ["HOCl", "H2SO4", "HNO3", "N2O", "Cl2O", "CH3OH", "NaCl", "MgO", "He", "H"])
def test_it_refuses_what_a_formula_cannot_tell(f):
    with pytest.raises(ValueError):
        chem.lewis(f)


def test_the_charge_can_ride_on_the_formula():
    assert chem.split_charge("NH4+", 0) == ("NH4", 1)
    assert chem.split_charge("CO3 2-", 0) == ("CO3", -2)
    assert chem.split_charge("CO3^2-", 0) == ("CO3", -2)
    assert chem.split_charge("OH", "-") == ("OH", -1)
    assert chem.lewis("NH4+")["charge"] == 1


# ---------------------------------------------------------------- the picture and the slate

ALL = [h[:2] for h in HAND] + [("O3", 0), ("XeF4", 0), ("SF6", 0), ("NO2", 0), ("H2O2", 0), ("N2H4", 0), ("C2H2", 0), ("ClF3", 0),
                               ("IF5", 0), ("CO", 0), ("NO", 0), ("PO4", -3)]


def _geo(f, q):
    s = slate.build({"blocks": [{"k": "diagram", "items": [{"t": "molecule", "formula": f, "charge": q}], "say": "x"}]})
    return s["blocks"][0]["geo"]


@pytest.mark.parametrize("f,q", ALL, ids=[f"{f}{q}" for f, q in ALL])
def test_the_picture_has_no_overlaps(f, q):
    g = _geo(f, q)
    assert slate_geo.overlaps(g) == []
    assert not any(l.get("crowded") for l in g["labels"])
    info = chem.lewis(f, q)
    assert sum(1 for l in g["labels"] if l.get("at")) == len(info["atoms"])
    assert len(g["dots"]) == 2 * info["lp_total"] + sum(a["rad"] for a in info["atoms"])      # two dots per lone pair


def test_the_answer_line_is_filled_by_code_and_a_wrong_shape_is_refused():
    d = {"k": "diagram", "items": [{"t": "molecule", "formula": "H2O"}], "say": "Water."}
    ok = {"k": "math", "tex": "\\ans", "ans": {"chem": "geom"}, "say": "It is \\geom."}
    s = slate.build({"blocks": [d, ok]})
    assert s["blocks"][1]["ans"]["value"] == "\\text{bent}" and s["blocks"][1]["say"] == "It is bent."
    wrong = {"k": "text", "text": "Water is linear.", "say": "Water is linear."}
    assert slate.build({"blocks": [d, wrong, ok]})["dropped"]
    with pytest.raises(ValueError):
        slate.build({"blocks": [d, {"k": "math", "tex": "\\text{linear}", "say": "x"}]})        # a wrong shape on the answer line kills it
    with pytest.raises(ValueError):
        slate.build({"blocks": [{"k": "math", "tex": "\\ans", "ans": {"chem": "geom"}, "say": "x"}]})   # no molecule above


def test_the_model_cannot_place_atoms():
    with pytest.raises(ValueError):
        slate.build({"blocks": [{"k": "diagram", "items": [{"t": "atom", "id": "a", "el": "O"}], "say": "x"}]})
    with pytest.raises(ValueError):
        slate.build({"blocks": [{"k": "diagram", "items": [{"t": "molecule", "formula": "HOCl"}], "say": "x"}]})


@pytest.mark.parametrize("formula,charge,words", [
    ("H2O", 0, "2 single bonds"), ("CO2", 0, "2 double bonds"), ("N2", 0, "1 triple bond"), ("O2", 0, "1 double bond"),
    ("HCN", 0, "1 single bond and 1 triple bond"), ("CH2O", 0, "2 single bonds and 1 double bond"), ("SO2", 0, "2 double bonds"),
])
def test_bond_words_name_the_order(formula, charge, words):
    assert chem.fact(chem.summary(chem.lewis(formula, charge)), "bonds") == words
