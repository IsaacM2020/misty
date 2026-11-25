"""Slate geometry: turns the model's diagram things (axes, plot, circle, angle, vector...) into exact coordinates.

The model never sends a coordinate. This works in a 0 to 1000 wide grid (height `h`, about 560) and returns
  strokes: pen lines, each {k, pts, closed, w, c, minor}   (a circle is a closed loop whose first point is its last)
  dots:    filled points {x, y}
  labels:  {s, x, y, w, h, tick} boxes (top-left, grid units), placed so no box touches a stroke, a dot or another box
The page only adds a light hand wobble (see wobble() in slate.js): at most 1.5 px, zero at every endpoint, periodic on closed
shapes, so lines that meet still meet and closed shapes stay closed. Molecules (S5) are laid out by molecule() below, from the Lewis structure chem.py works out."""
from __future__ import annotations

import math
import re

W = 1000.0
H = 560.0
ML, MR, MT, MB = 80.0, 40.0, 36.0, 64.0          # room for tick numbers (left, bottom) and the arrow heads
FS = 44.0                                         # label font size, grid units
CHAR = 0.58                                       # average glyph width / font size (a little generous)
PAD = 5.0                                         # clear space around every label box
ARC_R = 140.0                                      # radius of an angle arc, grid units
DOT_R = 7.0
DRAWABLE = {"axes", "plot", "point", "line", "arrow", "vector", "circle", "angle", "label", "curve_pair"}
DIRS = [(1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1)]     # E NE N NW W SW S SE (y down)
_WORDS = {"theta": "θ", "alpha": "α", "beta": "β", "phi": "φ", "lambda": "λ", "delta": "Δ", "omega": "ω", "mu": "μ"}


def pretty(s: str) -> str:
    if "$" in s:
        return str(s)
    s = re.sub(r"\b(" + "|".join(_WORDS) + r")\b", lambda m: _WORDS[m.group(1)], str(s))
    s = re.sub(r"\bpi\b", "π", s)
    return re.sub(r"(\d)\s*(?:deg|degrees)\b", r"\1°", s)


def _tex_len(s: str) -> float:
    """Rough rendered width, in characters, of a label that may hold `$...$` maths. A fraction counts as its wider half."""
    n = 0.0
    for i, part in enumerate(re.split(r"\$", s)):
        if i % 2 == 0:
            n += len(part)
            continue
        part = re.sub(r"\\(?:sin|cos|tan|ln|log)", "xxx", part)
        part = re.sub(r"\\sqrt(?!\{)", "x", part)
        part = re.sub(r"\\[td]?frac(\d)(\d)", "x", part)
        for _ in range(4):
            part = re.sub(r"\\[td]?frac\{([^{}]*)\}\{([^{}]*)\}", lambda m: max(m.group(1), m.group(2), key=len) + "x", part)
            part = re.sub(r"\\sqrt\{([^{}]*)\}", lambda m: "x" + m.group(1), part)
        part = re.sub(r"\\(?:text|mathrm|vec|left|right)", "", part)
        part = re.sub(r"\\[a-zA-Z]+", "x", part)
        part = re.sub(r"[{}^_\\ ]", "", part)
        n += sum(1.0 if c.isalnum() else 1.7 if c in "=+-" else 0.7 for c in part) + 0.5
    return n


def box_size(s: str) -> tuple[float, float]:
    tex = "$" in s
    tall = tex and re.search(r"frac|sqrt|vec", s)
    return round(max(1.0, _tex_len(s)) * FS * (0.62 if tex else CHAR) + 6, 1), round(FS * (1.52 if tall else 1.4 if tex else 1.15), 1)


def _nice_step(span: float, target: int = 5) -> float:
    raw = span / target
    p = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * p * 1.0001:
            return m * p
    return 10 * p


def _fmt(v: float) -> str:
    v = round(v, 6)
    return str(int(v)) if v == int(v) else f"{v:g}"


class _Map:
    def __init__(self, xr, yr, equal: bool, h: float = H):
        px0, px1, py0, py1 = ML, W - MR, MT, h - MB
        sx, sy = (px1 - px0) / (xr[1] - xr[0]), (py1 - py0) / (yr[1] - yr[0])
        if equal:
            sx = sy = min(sx, sy)
        self.sx, self.sy = sx, sy
        self.ox = (px0 + px1) / 2 - sx * (xr[0] + xr[1]) / 2
        self.oy = (py0 + py1) / 2 + sy * (yr[0] + yr[1]) / 2
        self.xr, self.yr = xr, yr

    def __call__(self, x, y):
        return (round(self.ox + x * self.sx, 2), round(self.oy - y * self.sy, 2))


def _view(items: list[dict]) -> tuple[list[float], list[float], bool]:
    ax = next((p for p in items if p["t"] == "axes"), None)
    equal = any(p["t"] in ("circle", "angle", "arrow", "vector") for p in items) or ax is None
    if ax:
        return list(map(float, ax["x"])), list(map(float, ax["y"])), equal
    xs, ys = [], []
    for p in items:
        t = p["t"]
        if t == "circle":
            xs += [p["x"] - p["r"], p["x"] + p["r"]]
            ys += [p["y"] - p["r"], p["y"] + p["r"]]
        elif t in ("point", "label", "angle"):
            xs.append(p["x"]), ys.append(p["y"])
        elif t in ("line", "arrow", "vector"):
            xs += [p["x1"], p["x2"]]
            ys += [p["y1"], p["y2"]]
    if not xs:
        xs, ys = [0, 1], [0, 1]

    def pad(lo, hi):
        e = max(0.12 * (hi - lo), 0.3)
        return [lo - e, hi + e]
    return pad(min(xs), max(xs)), pad(min(ys), max(ys)), True


def _dense(pts, step=4.0):
    """The polyline resampled every `step` units, for hit tests."""
    out = [tuple(pts[0])]
    for a, b in zip(pts, pts[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        for i in range(1, int(d // step) + 1):
            t = i * step / d
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out.append(tuple(b))
    return out


def _hits(box, pts, pad):
    x0, y0, x1, y1 = box[0] - pad, box[1] - pad, box[0] + box[2] + pad, box[1] + box[3] + pad
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in pts)


def _boxes_touch(a, b, pad):
    return not (a[0] + a[2] + pad <= b[0] or b[0] + b[2] + pad <= a[0] or a[1] + a[3] + pad <= b[1] or b[1] + b[3] + pad <= a[1])


class _Page:
    def __init__(self, h: float = H):
        self.h = h
        self.strokes: list[dict] = []
        self.dots: list[dict] = []
        self.labels: list[dict] = []
        self.hit: list[tuple[float, float]] = []

    def stroke(self, k, pts, w=6.0, c="ink", closed=False, minor=False):
        pts = [[round(x, 2), round(y, 2)] for x, y in pts]
        if closed:
            pts[-1] = list(pts[0])
        self.strokes.append({"k": k, "pts": pts, "closed": closed, "w": w, "c": c, "minor": minor})
        self.hit += _dense(pts)

    def dot(self, x, y, c="ink", r=DOT_R):
        self.dots.append({"x": round(x, 2), "y": round(y, 2), "c": c, "r": r})
        self.hit += [(x + dx * r, y + dy * r) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]

    def fixed(self, s, cx, cy, size=None, **extra):
        """A label centred on a point (an atom symbol): not placed, so it is never moved."""
        w, h = size or box_size(s)
        lab = {"s": s, "x": round(cx - w / 2, 1), "y": round(cy - h / 2, 1), "w": w, "h": h, "tick": False, **extra}
        self.labels.append(lab)
        return lab

    def free(self, box, pad=PAD):
        if box[0] < 6 or box[1] < 4 or box[0] + box[2] > W - 6 or box[1] + box[3] > self.h - 4:
            return False
        if _hits(box, self.hit, pad):
            return False
        return not any(_boxes_touch(box, (l["x"], l["y"], l["w"], l["h"]), pad) for l in self.labels)

    def label(self, s, ax, ay, prefer=1, gap=16.0, optional=False, tick=False):
        """Try the 8 spots around the anchor, nearest the preferred one first; if none is clear, move outward."""
        s = pretty(s)
        if not s:
            return None
        w, h = box_size(s)
        cands = []
        for ring in range(0, 30 if not optional else 4):
            for i in range(8):
                turn = min((i - prefer) % 8, (prefer - i) % 8)
                cands.append((gap + ring * 8 + 16 * turn, gap + ring * 8, i))
        for _, gg, i in sorted(cands):
            dx, dy = DIRS[i]
            n = math.hypot(dx, dy)
            ox, oy = dx / n * gg, dy / n * gg
            bx = ax + ox if dx > 0 else ax + ox - w if dx < 0 else ax - w / 2
            by = ay + oy if dy > 0 else ay + oy - h if dy < 0 else ay - h / 2
            box = (round(min(max(bx, 8.0), W - 8.0 - w), 1), round(min(max(by, 6.0), self.h - 6.0 - h), 1), w, h)
            if self.free(box):
                lab = {"s": s, "x": box[0], "y": box[1], "w": w, "h": h, "tick": tick}
                self.labels.append(lab)
                return lab
        if optional:
            return None
        lab = {"s": s, "x": round(min(max(ax + gap, 8.0), W - 8.0 - w), 1), "y": round(min(max(ay - h - gap, 6.0), self.h - 6.0 - h), 1),
               "w": w, "h": h, "tick": tick, "crowded": True}
        self.labels.append(lab)
        return lab


def _col(p: dict) -> str:
    return "red" if p.get("c") == "red" else "ink"


def _dir_of(dx, dy) -> int:
    """Nearest of the 8 spots for a direction in grid units (y down)."""
    return int(round(math.atan2(-dy, dx) / (math.pi / 4))) % 8


def _axes(pg: _Page, m: _Map, p: dict, ticks: list, pend: list):
    (x0, x1), (y0, y1) = m.xr, m.yr
    ycross = 0.0 if y0 <= 0 <= y1 else y0
    xcross = 0.0 if x0 <= 0 <= x1 else x0
    a, b = m(x0, ycross), m(x1, ycross)
    pg.stroke("axis", [a, b], 4.0, "axis")
    pg.stroke("head", [(b[0] - 16, b[1] - 9), b, (b[0] - 16, b[1] + 9)], 4.0, "axis", minor=True)
    c, d = m(xcross, y0), m(xcross, y1)
    pg.stroke("axis", [c, d], 4.0, "axis")
    pg.stroke("head", [(d[0] - 9, d[1] + 16), d, (d[0] + 9, d[1] + 16)], 4.0, "axis", minor=True)
    for lo, hi, horiz in ((x0, x1, True), (y0, y1, False)):
        step = _nice_step(hi - lo)
        v = math.ceil(lo / step - 1e-9) * step
        while v <= hi + 1e-9:
            if abs(v) > 1e-9:
                if horiz:
                    gx, gy = m(v, ycross)
                    pg.stroke("tick", [(gx, gy - 7), (gx, gy + 7)], 3.2, "axis", minor=True)
                    ticks.append((_fmt(v), gx, gy + 7, 6, 4.0))
                else:
                    gx, gy = m(xcross, v)
                    pg.stroke("tick", [(gx - 7, gy), (gx + 7, gy)], 3.2, "axis", minor=True)
                    ticks.append((_fmt(v), gx - 7, gy, 4, 4.0))
            v = round(v + step, 10)
    if p.get("xl"):
        pend.append((p["xl"], b[0], b[1], 2, 14.0))
    if p.get("yl"):
        pend.append((p["yl"], d[0], d[1], 0, 14.0))


def _plot(pg: _Page, m: _Map, p: dict, sample, pend: list):
    lo, hi = float(p.get("from", m.xr[0])), float(p.get("to", m.xr[1]))
    lo, hi = max(lo, m.xr[0]), min(hi, m.xr[1])
    last = None
    for seg in sample(p["f"], lo, hi, m.yr[0], m.yr[1]):
        pts = [m(x, y) for x, y in seg]
        keep = [pts[0]]
        for q in pts[1:]:
            if math.hypot(q[0] - keep[-1][0], q[1] - keep[-1][1]) >= 9.0:
                keep.append(q)
        if keep[-1] != pts[-1]:
            keep.append(pts[-1])
        run: list = []
        for q in keep + [None]:                                  # clip to the picture
            if q is not None and MT - 4 <= q[1] <= pg.h - MB + 4:
                run.append(q)
            else:
                if len(run) > 1:
                    pg.stroke("curve", run, 6.4, _col(p))
                    last = run
                run = []
    if last and p.get("label"):
        i = max(0, int(len(last) * 0.86) - 1)
        pend.append((p["label"], last[i][0], last[i][1], 1, 16.0, _col(p)))


def layout(items: list[dict], sample) -> dict | None:
    """items: the checked diagram things. sample: draw.sample. Returns the geo dict, or None if nothing is drawable yet."""
    mol = next((p for p in items if p["t"] == "molecule"), None)
    if mol:
        return molecule(mol["info"])
    items = [p for p in items if p["t"] in DRAWABLE]
    if not items:
        return None
    cp = next((p for p in items if p["t"] == "curve_pair"), None)
    pend: list = []                                              # (text, ax, ay, prefer, gap): the model's own labels
    ticks: list = []                                             # tick numbers: optional, dropped if they would collide
    curves: list = []
    h = H
    if cp:
        sd = cp["kind"] == "supply_demand"
        names = (list(cp.get("labels") or []) + ["", ""]) if True else []
        items = [{"t": "axes", "x": [0, 10], "y": [0, 10], "xl": "Q" if sd else "Good A", "yl": "P" if sd else "Good B"}] + \
                [p for p in items if p["t"] not in ("curve_pair", "axes")]
        m = _Map([0.0, 10.0], [0.0, 10.0], False, h)
        if sd:
            curves = [([(0.6, 1.48), (5, 5), (9.2, 8.36)], names[0] or "S", 9.2, 8.36, 0),
                      ([(0.6, 8.52), (5, 5), (9.2, 1.64)], names[1] or "D", 9.2, 1.64, 6)]
        else:
            pts = [(8 * math.sin(math.radians(a)), 8 * math.cos(math.radians(a))) for a in range(0, 91, 6)]
            curves = [(pts, names[0], pts[1][0], pts[1][1], 1)]
    else:
        xr, yr, equal = _view(items)
        if equal:                                                # fit the page to the drawing, so it fills the box
            h = min(920.0, max(440.0, MT + MB + (yr[1] - yr[0]) / (xr[1] - xr[0]) * (W - ML - MR)))
        m = _Map(xr, yr, equal, h)
    pg = _Page(h)
    for p in items:
        if p["t"] == "axes":
            _axes(pg, m, p, ticks, pend)
    for pts, name, lx, ly, pref in curves:
        pg.stroke("curve", [m(x, y) for x, y in pts], 6.4)
        if name:
            gx, gy = m(lx, ly)
            pend.append((name, gx, gy, pref, 14.0))
    for p in items:
        t = p["t"]
        if t == "plot":
            _plot(pg, m, p, sample, pend)
        elif t == "circle":
            cx, cy = m(p["x"], p["y"])
            r = p["r"] * m.sx
            pts = [(cx + r * math.cos(2 * math.pi * i / 64), cy - r * math.sin(2 * math.pi * i / 64)) for i in range(65)]
            pg.stroke("circle", pts, 6.4, _col(p), closed=True)
            if p.get("label"):
                pend.append((p["label"], cx + r * 0.7071, cy - r * 0.7071, 1, 14.0, _col(p)))
        elif t in ("line", "arrow", "vector"):
            a, b = m(p["x1"], p["y1"]), m(p["x2"], p["y2"])
            if t == "line" and p.get("dashed"):
                n = max(3, int(math.hypot(b[0] - a[0], b[1] - a[1]) // 26))
                for i in range(n):
                    u0, u1 = (i + 0.1) / n, (i + 0.9) / n
                    pg.stroke("dash", [(a[0] + (b[0] - a[0]) * u0, a[1] + (b[1] - a[1]) * u0),
                                       (a[0] + (b[0] - a[0]) * u1, a[1] + (b[1] - a[1]) * u1)], 5.0, _col(p), minor=True)
            else:
                pg.stroke("line", [a, b], 6.4 if _col(p) == "red" else 6.0, _col(p))
            if t != "line":
                ang = math.atan2(b[1] - a[1], b[0] - a[0])
                pg.stroke("head", [(b[0] - 22 * math.cos(ang - 0.45), b[1] - 22 * math.sin(ang - 0.45)), b,
                                   (b[0] - 22 * math.cos(ang + 0.45), b[1] - 22 * math.sin(ang + 0.45))], 6.0, _col(p), minor=True)
            if p.get("label"):
                if t == "line":
                    pend.append((p["label"], (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, _dir_of(-(b[1] - a[1]), b[0] - a[0]), 12.0, _col(p)))
                else:
                    pend.append((p["label"], b[0], b[1], _dir_of(b[0] - a[0], b[1] - a[1]), 14.0, _col(p)))
        elif t == "angle":
            cx, cy = m(p["x"], p["y"])
            a0, a1 = math.radians(p["from"]), math.radians(p["to"])
            n = max(6, int(abs(a1 - a0) / math.radians(6)))
            pg.stroke("arc", [(cx + ARC_R * math.cos(a0 + (a1 - a0) * i / n), cy - ARC_R * math.sin(a0 + (a1 - a0) * i / n))
                              for i in range(n + 1)], 5.0, _col(p))
            if p.get("label"):
                mid = (a0 + a1) / 2
                pend.insert(0, (p["label"], cx + ARC_R * math.cos(mid), cy - ARC_R * math.sin(mid), _dir_of(math.cos(mid), -math.sin(mid)), 12.0, _col(p)))
        elif t == "point":
            gx, gy = m(p["x"], p["y"])
            pg.dot(gx, gy, _col(p))
            if p.get("label"):
                pend.insert(0, (p["label"], gx, gy, 1, 14.0, _col(p)))
        elif t == "label":
            gx, gy = m(p["x"], p["y"])
            pend.append((p["s"], gx, gy, 0, 4.0, _col(p)))
    for s, ax, ay, pref, gap, *col in pend:
        lab = pg.label(s, ax, ay, pref, gap)
        if lab and col and col[0] == "red":
            lab["c"] = "red"
    for s, ax, ay, pref, gap in ticks:
        pg.label(s, ax, ay, pref, gap, optional=True, tick=True)
    return {"w": W, "h": h, "sx": round(m.sx, 4), "sy": round(m.sy, 4), "strokes": pg.strokes, "dots": pg.dots, "labels": pg.labels}


def overlaps(geo: dict, pad: float = 2.0) -> list[str]:
    """The done-check: every label box against every stroke, dot and other box, on a finer sample than the placer used."""
    hit = []
    for s in geo["strokes"]:
        hit += _dense(s["pts"], 1.5)
    for d in geo["dots"]:
        r = d.get("r", DOT_R)
        hit += [(d["x"] + dx * r, d["y"] + dy * r) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    bad = []
    labs = geo["labels"]
    for i, l in enumerate(labs):
        box = (l["x"], l["y"], l["w"], l["h"])
        if _hits(box, hit, pad):
            bad.append(f"{l['s']!r} touches a stroke")
        if box[0] < 0 or box[1] < 0 or box[0] + box[2] > geo["w"] or box[1] + box[3] > geo["h"]:
            bad.append(f"{l['s']!r} is off the page")
        for o in labs[i + 1:]:
            if _boxes_touch(box, (o["x"], o["y"], o["w"], o["h"]), pad):
                bad.append(f"{l['s']!r} touches {o['s']!r}")
    return bad


# ---------------------------------------------------------------- S5: a molecule, drawn from its Lewis structure (chem.py)
# bond positions (angle in degrees, y up; style p = in the page, w = towards you, d = away), lone-pair positions, the angle arc.
# Keyed by (bonded atoms, lone domains). Two bonds + two lone pairs uses the true 104.5 degrees; the rest are flat drawings
# with wedges and dashes where the shape is 3D.
_T = {
    (2, 0): ([(0, "p"), (180, "p")], [], None),
    (3, 0): ([(90, "p"), (210, "p"), (330, "p")], [], (210, 330)),
    (2, 1): ([(211, "p"), (329, "p")], [90], (211, 329)),
    (4, 0): ([(35.25, "p"), (144.75, "p"), (315, "w"), (225, "d")], [], (35.25, 144.75)),
    (3, 1): ([(35.25, "p"), (144.75, "p"), (315, "w")], [225], (35.25, 144.75)),
    (2, 2): ([(217.75, "p"), (322.25, "p")], [50, 130], (217.75, 322.25)),
    (5, 0): ([(90, "p"), (270, "p"), (180, "p"), (340, "w"), (20, "d")], [], (90, 180)),
    (4, 1): ([(90, "p"), (270, "p"), (180, "p"), (340, "w")], [20], (90, 180)),
    (3, 2): ([(90, "p"), (270, "p"), (180, "p")], [340, 20], (90, 180)),
    (2, 3): ([(90, "p"), (270, "p")], [180, 340, 20], None),
    (6, 0): ([(90, "p"), (270, "p"), (180, "p"), (0, "p"), (225, "w"), (45, "d")], [], (0, 90)),
    (5, 1): ([(90, "p"), (180, "p"), (0, "p"), (225, "w"), (45, "d")], [270], (0, 90)),
    (4, 2): ([(90, "p"), (270, "p"), (180, "p"), (0, "p")], [225, 45], (0, 90)),
}
_TERM = {1: [180], 2: [90, -90], 3: [90, 180, -90]}          # lone-pair directions at a terminal atom, relative to its bond
LB_MAX, H_MAX = 330.0, 600.0


def _u(deg: float) -> tuple[float, float]:
    return math.cos(math.radians(deg)), -math.sin(math.radians(deg))


def _wrap(a: float) -> float:
    return (a + 180) % 360 - 180


def _angle_tex(a: str | None) -> str | None:
    if not a:
        return None
    return "$" + ", ".join(p.replace("<", "<") + "^\\circ" for p in a.split(",")) + "$"


def _charge_tex(q: int) -> str:
    return "$" + ("" if abs(q) == 1 else str(abs(q))) + ("+" if q > 0 else "-") + "$"


def molecule(info: dict) -> dict:
    atoms, n = info["atoms"], len(info["atoms"])
    raw: dict[int, tuple[float, float]] = {}                # atom -> position in bond lengths (y down)
    bonds: list[tuple[int, int, int, str, float]] = []      # a, b, order, style, angle of a->b
    lone: list[tuple[int, float, str]] = []                 # atom, direction, pair|dot
    arcs: list[tuple[int, float, float, str | None]] = []
    cs = info["centres"]
    order = {(min(b["a"], b["b"]), max(b["a"], b["b"])): b["n"] for b in info["bonds"]}

    def bo(a, b):
        return order[(min(a, b), max(a, b))]

    def en(i):
        from .chem import EL
        return EL[atoms[i]["el"]][1]

    def place_centre(c, at, other=None, target=0.0):
        a = atoms[c]
        nb = a["nbrs"]
        nlp = a["lp"] + a["rad"]
        bpos, lps, arc = _T[(len(nb), nlp)]
        rot = 0.0
        if other is not None:                               # turn the shape so one bond points at the other centre
            k = min(range(len(bpos)), key=lambda i: abs(_wrap(target - bpos[i][0])))
            rot = _wrap(target - bpos[k][0])
        rest = sorted([j for j in nb if j != other], key=lambda j: (-bo(c, j), -en(j), j))
        chosen = ([other] if other is not None else []) + rest
        slots = list(range(len(bpos)))
        if other is not None:
            slots.remove(k)
            slots.insert(0, k)
        for j, si in zip(chosen, slots):
            ang, st = bpos[si]
            ang = ang + rot
            raw[j] = (raw[c][0] + _u(ang)[0], raw[c][1] + _u(ang)[1])
            bonds.append((c, j, bo(c, j), "p" if j == other else st, ang))
        for k2, ang in enumerate(lps):
            lone.append((c, ang + rot, "pair" if k2 < a["lp"] else "dot"))
        pa = info["per_centre"][cs.index(c)]["angle"]
        if arc and pa:
            arcs.append((c, arc[0] + rot, arc[1] + rot, _angle_tex(pa)))
        elif not arc and pa == "180":
            arcs.append((c, 0.0, 0.0, _angle_tex("180")))

    def terminal_lone(t, toward):                           # toward = direction from t to its neighbour
        a = atoms[t]
        d = a["lp"] + a["rad"]
        for k, rel in enumerate(_TERM.get(d, [])):
            lone.append((t, toward + rel, "pair" if k < a["lp"] else "dot"))

    if not cs:                                              # two atoms in a row
        raw[0], raw[1] = (-0.5, 0.0), (0.5, 0.0)
        bonds.append((0, 1, bo(0, 1), "p", 0.0))
        terminal_lone(0, 0.0)
        terminal_lone(1, 180.0)
    elif len(cs) == 1:
        raw[cs[0]] = (0.0, 0.0)
        place_centre(cs[0], (0.0, 0.0))
    else:
        raw[cs[0]], raw[cs[1]] = (-0.5, 0.0), (0.5, 0.0)
        place_centre(cs[0], None, cs[1], 0.0)
        place_centre(cs[1], None, cs[0], 180.0)
    for (a, b, o, st, ang) in list(bonds):
        if b not in cs and cs:
            terminal_lone(b, ang + 180)

    xs, ys = [p[0] for p in raw.values()], [p[1] for p in raw.values()]
    spanx, spany = max(xs) - min(xs), max(ys) - min(ys)
    q = info["charge"]
    mx, my = 105.0 + (50 if q else 0), 110.0 + (24 if q else 0)
    lb = LB_MAX
    if spanx:
        lb = min(lb, (W - 2 * mx) / spanx)
    if spany:
        lb = min(lb, (H_MAX - 2 * my) / spany)
    h = max(380.0, spany * lb + 2 * my)
    ox, oy = W / 2 - (max(xs) + min(xs)) / 2 * lb, h / 2 - (max(ys) + min(ys)) / 2 * lb
    P = {i: (round(ox + raw[i][0] * lb, 2), round(oy + raw[i][1] * lb, 2)) for i in raw}
    pg = _Page(h)
    tex = {i: "$\\mathrm{" + atoms[i]["el"] + "}$" for i in range(n)}
    box = {i: (round(FS * (0.74 * len(atoms[i]["el"]) + 0.1) + 6, 1), round(FS * 1.05, 1)) for i in range(n)}

    def edge(i, ang):                                       # distance from the atom's centre to its label box, along ang
        w, hh = box[i]
        dx, dy = abs(math.cos(math.radians(ang))), abs(math.sin(math.radians(ang)))
        return min((w / 2) / dx if dx > 1e-6 else 1e9, (hh / 2) / dy if dy > 1e-6 else 1e9)

    for i in range(n):
        pg.fixed(tex[i], *P[i], size=box[i], at=True)
    pts_all = [P[i] for i in range(n)]
    for (a, b, o, st, ang) in bonds:
        ux, uy = _u(ang)
        gap = 10.0 if o == 1 else 15.0
        s = (P[a][0] + ux * (edge(a, ang) + gap), P[a][1] + uy * (edge(a, ang) + gap))
        e = (P[b][0] - ux * (edge(b, ang + 180) + gap), P[b][1] - uy * (edge(b, ang + 180) + gap))
        nx, ny = -uy, ux
        if st == "w":
            pg.stroke("wedge", [s, (e[0] + nx * 12, e[1] + ny * 12), (e[0] - nx * 12, e[1] - ny * 12), s], 4.4, closed=True)
            pg.stroke("line", [s, e], 4.4, minor=True)
        elif st == "d":
            for k in range(1, 7):
                t = k / 7
                cx, cy, hw = s[0] + (e[0] - s[0]) * t, s[1] + (e[1] - s[1]) * t, 3 + 9 * t
                pg.stroke("dash", [(cx - nx * hw, cy - ny * hw), (cx + nx * hw, cy + ny * hw)], 4.6, minor=True)
        else:
            for off in {1: [0], 2: [-9, 9], 3: [-14, 0, 14]}[o]:
                pg.stroke("line", [(s[0] + nx * off, s[1] + ny * off), (e[0] + nx * off, e[1] + ny * off)], 6.0)
    for (i, ang, kind) in lone:
        ux, uy = _u(ang)
        d = edge(i, ang) + 34
        cx, cy = P[i][0] + ux * d, P[i][1] + uy * d
        if kind == "pair":
            for sgn in (-1, 1):
                pg.dot(cx - uy * 11 * sgn, cy + ux * 11 * sgn, "red", 6.5)
        else:
            pg.dot(cx, cy, "red", 6.5)
        pts_all.append((cx, cy))
    if q:                                                   # an ion sits in square brackets with its charge
        x0 = min(p[0] for p in pts_all) - 62
        x1 = max(p[0] for p in pts_all) + 62
        y0 = min(p[1] for p in pts_all) - 52
        y1 = max(p[1] for p in pts_all) + 52
        pg.stroke("bracket", [(x0 + 18, y0), (x0, y0), (x0, y1), (x0 + 18, y1)], 5.0)
        pg.stroke("bracket", [(x1 - 18, y0), (x1, y0), (x1, y1), (x1 - 18, y1)], 5.0)
        pg.label(_charge_tex(q), x1, y0, 1, 4.0)
    for (c, a0, a1, text) in arcs:
        if not text or (len(cs) > 1 and arcs.index((c, a0, a1, text)) > 0):
            continue                                         # one angle label is enough on a two-centre molecule
        if a0 == a1:                                        # a straight molecule: just say 180 below the middle
            pg.label(text, P[c][0], P[c][1] + box[c][1] / 2 + 40, 6, 6.0)["c"] = "red"
            continue
        r = min(110.0, 0.42 * lb)
        d = _wrap(a1 - a0)
        m = max(6, int(abs(d) / 6))
        pg.stroke("arc", [(P[c][0] + r * _u(a0 + d * k / m)[0], P[c][1] + r * _u(a0 + d * k / m)[1]) for k in range(m + 1)], 5.0, "red")
        mid = a0 + d / 2
        lab = pg.label(text, P[c][0] + r * _u(mid)[0], P[c][1] + r * _u(mid)[1], int(round((mid % 360) / 45)) % 8, 12.0)
        if lab:
            lab["c"] = "red"
    for i in range(n):
        f = atoms[i]["fc"]
        if f:
            lab = pg.label(_charge_tex(f), P[i][0], P[i][1], 1, 8.0)
            if lab:
                lab["c"] = "red"
    return {"w": W, "h": h, "sx": round(lb, 4), "sy": round(lb, 4), "strokes": pg.strokes, "dots": pg.dots, "labels": pg.labels}
