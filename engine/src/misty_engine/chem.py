"""Chemistry for the Slate (S5): a Lewis structure from a formula, worked out by code.

Valence electrons, the skeleton, bond orders, lone pairs, formal charges and the geometry name all come from here, never
from the model. The method is the textbook one: count valence electrons, put the least electronegative atom in the middle,
join with single bonds, then try every way of raising bond orders and keep the structure that obeys the octet rule (with
the usual exceptions: B/Be/Al may stop short, period 3+ centres may expand) and has the smallest formal charges, with any
negative charge on the most electronegative atoms. Anything it cannot do honestly raises ValueError instead of guessing."""
from __future__ import annotations

import itertools
import re

# symbol: (valence electrons, Pauling electronegativity, period)
EL = {
    "H": (1, 2.20, 1), "Be": (2, 1.57, 2), "B": (3, 2.04, 2), "C": (4, 2.55, 2), "N": (5, 3.04, 2), "O": (6, 3.44, 2),
    "F": (7, 3.98, 2), "Al": (3, 1.61, 3), "Si": (4, 1.90, 3), "P": (5, 2.19, 3), "S": (6, 2.58, 3), "Cl": (7, 3.16, 3),
    "As": (5, 2.18, 4), "Se": (6, 2.55, 4), "Br": (7, 2.96, 4), "Kr": (8, 3.00, 4), "I": (7, 2.66, 5), "Xe": (8, 2.60, 5),
}
METALS = {"Li", "Na", "K", "Mg", "Ca", "Fe", "Cu", "Zn", "Ag", "Au", "Pb", "Sn", "Ba", "Mn", "Cr", "Ni", "Co", "Rb", "Cs", "Sr", "Ti"}
SHORT_OK = {"B", "Be", "Al"}                       # may stop at 6 (or 4) electrons
HALOGENS = {"F", "Cl", "Br", "I"}
MAX_ATOMS = 12
NAMES = {"bent", "linear", "trigonal planar", "tetrahedral", "trigonal pyramidal", "trigonal bipyramidal", "octahedral",
         "seesaw", "t-shaped", "square planar", "square pyramidal"}
DOMAIN_NAMES = {2: "linear", 3: "trigonal planar", 4: "tetrahedral", 5: "trigonal bipyramidal", 6: "octahedral"}
# (bonded atoms, lone domains) -> molecular geometry
GEOMETRY = {
    (1, 0): "linear", (1, 1): "linear", (1, 2): "linear", (1, 3): "linear",
    (2, 0): "linear", (3, 0): "trigonal planar", (2, 1): "bent", (4, 0): "tetrahedral", (3, 1): "trigonal pyramidal",
    (2, 2): "bent", (5, 0): "trigonal bipyramidal", (4, 1): "seesaw", (3, 2): "t-shaped", (2, 3): "linear",
    (6, 0): "octahedral", (5, 1): "square pyramidal", (4, 2): "square planar",
}


def el(sym: str) -> tuple:
    return EL[sym]


# ---------------------------------------------------------------- reading the formula

def split_charge(formula, charge=0) -> tuple[str, int]:
    """'NH4+' / 'CO3 2-' / 'CO3^2-' / ('NH4', 1) / ('CO3', '2-') -> (formula without the charge, integer charge)."""
    f = str(formula).strip().replace("{", "").replace("}", "").replace("−", "-")
    q = 0
    m = re.fullmatch(r"(.+?)(?:[\s^]+(\d+))?\s*([+-])", str(formula).strip().replace("−", "-").replace("{", "").replace("}", ""))
    if m:
        f, q = m.group(1).strip(), (int(m.group(2) or 1)) * (1 if m.group(3) == "+" else -1)
    if isinstance(charge, str):
        c = charge.strip().replace("−", "-")
        m = re.fullmatch(r"([+-]?)\s*(\d*)\s*([+-]?)", c)
        if not m or not (m.group(1) or m.group(3) or m.group(2)):
            raise ValueError(f"can't read the charge {charge!r}")
        sign = m.group(1) or m.group(3) or "+"
        charge = int(m.group(2) or 1) * (1 if sign == "+" else -1)
    if not isinstance(charge, (int, float)) or isinstance(charge, bool) or charge != int(charge):
        raise ValueError("charge must be a whole number")
    return f.replace(" ", ""), int(charge) or q


def parse(formula: str) -> list[str]:
    """'CO3' -> ['C','O','O','O']; brackets multiply."""
    def count(s: str) -> dict:
        out: dict = {}
        i = 0
        while i < len(s):
            if s[i] == "(":
                depth, j = 1, i + 1
                while j < len(s) and depth:
                    depth += (s[j] == "(") - (s[j] == ")")
                    j += 1
                if depth:
                    raise ValueError("unbalanced bracket")
                inner = count(s[i + 1:j - 1])
                m = re.match(r"\d+", s[j:])
                k = int(m.group()) if m else 1
                for a, n in inner.items():
                    out[a] = out.get(a, 0) + n * k
                i = j + (len(m.group()) if m else 0)
                continue
            m = re.match(r"([A-Z][a-z]?)(\d*)", s[i:])
            if not m:
                raise ValueError(f"{formula!r} is not a formula")
            out[m.group(1)] = out.get(m.group(1), 0) + int(m.group(2) or 1)
            i += len(m.group())
        return out
    atoms: list[str] = []
    for a, n in count(formula).items():
        if a in METALS:
            raise ValueError(f"{a} is a metal: {formula} is ionic, not a Lewis structure")
        if a not in EL:
            raise ValueError(f"I don't have {a} in my table")
        atoms += [a] * n
    if len(atoms) < 2:
        raise ValueError("a Lewis structure needs at least two atoms")
    if len(atoms) > MAX_ATOMS:
        raise ValueError(f"at most {MAX_ATOMS} atoms")
    return atoms


# ---------------------------------------------------------------- the skeleton

def _skeleton(atoms: list[str]) -> tuple[list[int], list[tuple[int, int]]]:
    """(centres, bonds): which atoms are in the middle and who is joined to whom."""
    n = len(atoms)
    idx = list(range(n))
    if n == 2:
        return [], [(0, 1)]
    cand = [i for i in idx if atoms[i] not in ("H", "F")]
    if not cand:
        raise ValueError("no atom can sit in the middle")
    elems = sorted({atoms[i] for i in cand}, key=lambda e: EL[e][1])
    others_h = any(a == "H" for a in atoms)
    for e in elems:                                                      # least electronegative single atom in the middle
        cs = [i for i in idx if atoms[i] == e]
        if len(cs) == 1:
            if e != elems[0] and any(atoms[i] == elems[0] for i in cand):
                raise ValueError("can't tell the skeleton from the formula alone")
            c = cs[0]
            rest = [i for i in idx if i != c]
            if others_h and any(atoms[i] != "H" for i in rest) and e not in ("C", "Si"):
                raise ValueError("the formula alone doesn't say where the H sits (oxoacid or hydroxide): give me the structure")
            return [c], [(c, i) for i in rest]
    e = elems[0]                                                         # no single centre: a chain of the same atom
    cs = [i for i in idx if atoms[i] == e]
    rest = [i for i in idx if atoms[i] != e]
    if len(cs) == 2:
        for sym in {atoms[i] for i in rest}:
            if sum(1 for i in rest if atoms[i] == sym) % 2:
                raise ValueError("can't tell the skeleton from the formula alone")
        bonds = [(cs[0], cs[1])]
        taken: dict = {}
        for i in rest:
            k = taken.setdefault(atoms[i], [])
            k.append(i)
        for sym, lst in taken.items():
            half = len(lst) // 2
            bonds += [(cs[0], i) for i in lst[:half]] + [(cs[1], i) for i in lst[half:]]
        return cs, bonds
    if len(cs) == 3 and not rest:
        return [cs[1]], [(cs[1], cs[0]), (cs[1], cs[2])]                 # O3: the middle atom is the centre
    raise ValueError("can't tell the skeleton from the formula alone")


# ---------------------------------------------------------------- electrons

def _cap(atoms, i, nbrs) -> int:
    e = atoms[i]
    p = EL[e][2]
    if e == "H":
        return 2
    if p == 2 or len(nbrs[i]) == 1:
        return 8
    return 12 if p == 3 else 14


def _try(atoms, bonds, orders, total, nbrs):
    """Every legal way to place the lone pairs (and one lone electron if the count is odd) for one choice of bond orders.
    Yields (lp, rad, b)."""
    n = len(atoms)
    b = [0] * n
    for (x, y), o in zip(bonds, orders):
        b[x] += o
        b[y] += o
    left = total - 2 * sum(orders)
    if left < 0:
        return
    pairs, rad = divmod(left, 2)
    cap = [_cap(atoms, i, nbrs) for i in range(n)]
    opts = []
    for i in range(n):
        o = []
        hi = (cap[i] - 2 * b[i]) // 2
        for lp in range(0, max(hi, 0) + 1):
            for r in (0, 1):
                if r > rad:
                    continue
                s = 2 * b[i] + 2 * lp + r
                if atoms[i] == "H":
                    ok = s == 2
                elif s > cap[i]:
                    ok = False
                elif r:
                    ok = s >= 7
                else:
                    ok = s >= 8 or (atoms[i] in SHORT_OK and s >= 2 * b[i] + 0 and lp == 0)
                if ok:
                    o.append((lp, r))
        if not o:
            return
        opts.append(o)
    pick = [None] * n

    def go(i, p, r):
        if i == n:
            if p == 0 and r == 0:
                yield [x[0] for x in pick], [x[1] for x in pick], b
            return
        for lp, rr in opts[i]:
            if lp <= p and rr <= r:
                pick[i] = (lp, rr)
                yield from go(i + 1, p - lp, r - rr)
    yield from go(0, pairs, rad)


def lewis(formula, charge=0) -> dict:
    """The Lewis structure of a formula. Raises ValueError when there is no honest answer."""
    formula, charge = split_charge(formula, charge)
    atoms = parse(formula)
    n = len(atoms)
    total = sum(EL[a][0] for a in atoms) - charge
    if total < 2 or total > 120:
        raise ValueError("that many electrons doesn't make a molecule")
    centres, bonds = _skeleton(atoms)
    nbrs = [[] for _ in atoms]
    for x, y in bonds:
        nbrs[x].append(y)
        nbrs[y].append(x)
    if any(atoms[i] == "H" and len(nbrs[i]) != 1 for i in range(n)):
        raise ValueError("an H can only have one bond")
    free = [k for k, (x, y) in enumerate(bonds) if not any(atoms[z] in HALOGENS | {"H"} and len(nbrs[z]) == 1 for z in (x, y))]
    best, best_key, forms = None, None, set()
    for combo in itertools.product((1, 2, 3), repeat=len(free)):
        orders = [1] * len(bonds)
        for k, o in zip(free, combo):
            orders[k] = o
        for lp, rad, b in _try(atoms, bonds, orders, total, nbrs):
            fc = [EL[atoms[i]][0] - 2 * lp[i] - rad[i] - b[i] for i in range(n)]
            key = (sum(abs(f) for f in fc), round(sum(f * EL[atoms[i]][1] for i, f in enumerate(fc)), 6))
            if best_key is None or key < best_key:
                best, best_key, forms = (orders, lp, rad, b, fc), key, {tuple(orders)}
            elif key == best_key:
                forms.add(tuple(orders))
    if not best:
        raise ValueError(f"no valid Lewis structure for {formula}{_sc(charge)} (octet rule can't be met)")
    orders, lp, rad, b, fc = best
    ties = len(forms)
    atoms_out = [{"i": i, "el": atoms[i], "lp": lp[i], "rad": rad[i], "fc": fc[i], "shell": 2 * b[i] + 2 * lp[i] + rad[i],
                  "nbrs": nbrs[i], "centre": i in centres} for i in range(n)]
    out = {"formula": formula, "charge": charge, "ve": total, "atoms": atoms_out,
           "bonds": [{"a": x, "b": y, "n": o} for (x, y), o in zip(bonds, orders)], "centres": centres,
           "resonance": ties > 1, "bond_total": sum(orders), "lp_total": sum(lp)}
    out["per_centre"] = [_centre_geometry(out, c) for c in centres]
    if not centres:                                                       # a two-atom molecule is a straight line
        out["per_centre"] = [{"i": 0, "geometry": "linear", "domains": "linear", "sn": 1, "bonded": 1, "lone": 0, "angle": None}]
    names = {c["geometry"] for c in out["per_centre"]}
    out["geometry"] = names.pop() if len(names) == 1 else ", ".join(c["geometry"] for c in out["per_centre"])
    out["domains"] = out["per_centre"][0]["domains"]
    out["angle"] = out["per_centre"][0]["angle"]
    out["notes"] = _notes(out)
    return out


def _sc(q: int) -> str:
    return "" if not q else (("+" if q > 0 else "-") if abs(q) == 1 else f"{abs(q)}{'+' if q > 0 else '-'}")


def _centre_geometry(info, c: int) -> dict:
    a = info["atoms"][c]
    bonded = len(a["nbrs"])
    lone = a["lp"] + a["rad"]                                             # a lone electron counts as a domain
    sn = bonded + lone
    geo = GEOMETRY.get((bonded, lone))
    if not geo:
        raise ValueError(f"no geometry for {bonded} bonded atoms and {lone} lone pairs")
    nbr_el = {info["atoms"][j]["el"] for j in a["nbrs"]}
    angle = {(2, 0): "180", (3, 0): "120", (4, 0): "109.5", (6, 0): "90", (2, 3): "180"}.get((bonded, lone))
    if (bonded, lone) == (2, 1):
        angle = None if a["rad"] else "<120"
    elif (bonded, lone) == (3, 1):
        angle = "107" if a["el"] == "N" else "<109.5"
    elif (bonded, lone) == (2, 2):
        angle = "104.5" if a["el"] == "O" and nbr_el == {"H"} else "<109.5"
    elif (bonded, lone) == (5, 0):
        angle = "90,120"
    elif (bonded, lone) == (4, 1):
        angle = "<90,<120"
    elif (bonded, lone) in ((3, 2), (5, 1)):
        angle = "<90"
    elif (bonded, lone) == (4, 2):
        angle = "90"
    if any(x["rad"] for x in info["atoms"]):
        angle = None                                                      # radicals (NO2, ClO2) have their own odd angles
    return {"i": c, "sn": sn, "bonded": bonded, "lone": lone, "geometry": geo, "domains": DOMAIN_NAMES.get(max(sn, 2), "linear"),
            "angle": angle}


def _notes(info) -> list[str]:
    out = []
    for a in info["atoms"]:
        if a["el"] in SHORT_OK and a["shell"] < 8:
            out.append(f"{a['el']} has only {a['shell']} electrons: an incomplete octet")
        if a["el"] != "H" and a["shell"] > 8:
            out.append(f"{a['el']} has {a['shell']} electrons: an expanded octet")
        if a["rad"]:
            out.append("an unpaired electron: a radical")
    if info["resonance"]:
        out.append("resonance: other equal structures exist; one is drawn")
    return out


# ---------------------------------------------------------------- facts the slate can quote (\geom, \ve ...)

def bond_words(orders: list[int]) -> str:
    """Bonds counted and named by order: '2 double bonds', '1 triple bond', '3 single bonds and 1 double bond'."""
    names = {1: "single", 2: "double", 3: "triple"}
    parts = [f"{n} {names[o]} bond{'s' if n != 1 else ''}" for o in (1, 2, 3) if (n := orders.count(o))]
    return " and ".join(parts) if len(parts) < 3 else ", ".join(parts[:-1]) + " and " + parts[-1]


def fact(info: dict, key: str) -> str:
    """The value of a quotable fact (from `summary`), as plain text."""
    if key in ("geom", "geometry"):
        return info["geometry"]
    if key in ("domains", "domain"):
        return info["domains"]
    if key in ("ve", "valence"):
        return str(info["ve"])
    if key == "bonds":
        return bond_words(info["orders"])
    if key in ("lp", "lps"):
        return str(info["lp"])
    if key == "pairs":
        return str(info["ve"] // 2)
    if key == "angle":
        return (info["angle"] or "").replace(",", ", ")
    raise ValueError(f"unknown chemistry fact {key!r}")


def fact_tex(info: dict, key: str) -> str:
    v = fact(info, key)
    if key in ("geom", "geometry", "domains", "domain"):
        return "\\text{" + v + "}"
    if key == "angle":
        if not v:
            raise ValueError("no angle for this molecule")
        return v + "^\\circ"
    return v


def summary(info: dict) -> dict:
    """The small dict that rides on the diagram block (the page does not need the atoms)."""
    return {"formula": info["formula"], "charge": info["charge"], "ve": info["ve"], "geometry": info["geometry"],
            "domains": info["domains"], "angle": info["angle"], "bonds": len(info["bonds"]), "orders": [b["n"] for b in info["bonds"]], "lp": info["lp_total"],
            "resonance": info["resonance"], "notes": info["notes"]}
