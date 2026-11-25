"""The drawing window's scenes. The model writes a small scene (steps, each with a caption and things to draw); this
checks it, samples the curves so the app only has to draw lines, and returns the clean scene. Nothing here is saved:
a follow-up ("now shift demand right") is a new scene, and the old one is already in the chat as the tool call."""
from __future__ import annotations

import math

MAX_STEPS = 8
MAX_ITEMS = 14             # per step
SAMPLES = 220
COLORS = {"ink", "blue", "red", "green", "orange", "purple", "pink", "gray"}
KINDS = {"axes", "curve", "vline", "hline", "dot", "seg", "text", "poly", "eq", "table"}
_DEFAULT_VIEW = ([-10.0, 10.0], [-10.0, 10.0])


def _text(v, n: int) -> str:
    return str(v if v is not None else "").strip()[:n]


def _num(v, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} must be a number")
    return float(v)


def _range(v, name: str) -> list[float]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"{name} must be [min, max]")
    a, b = _num(v[0], name), _num(v[1], name)
    if not a < b:
        raise ValueError(f"{name} needs min below max")
    return [a, b]


def _color(v) -> str:
    return v if v in COLORS else "ink"


def _fixup(expr: str) -> str:
    """'3x/(x-2)' and '2x^2' are how people write it: add the missing times signs."""
    import re
    s = str(expr).replace(" ", "")
    s = re.sub(r"(\d|\))(?=[xX(])", r"\1*", s)
    s = re.sub(r"(?<=x)(?=\()", "*", s.replace("X", "x"))
    return s


def sample(expr: str, lo: float, hi: float, ylo: float, yhi: float) -> list[list[list[float]]]:
    """The curve as separate pieces: it breaks where the function does not exist or jumps (asymptotes)."""
    from .chat import calc
    e = _fixup(expr)
    span = yhi - ylo
    segs: list[list[list[float]]] = []
    cur: list[list[float]] = []
    prev: float | None = None
    for i in range(SAMPLES + 1):
        x = lo + (hi - lo) * i / SAMPLES
        try:
            y = float(calc(e, {"x": x}))
        except (ValueError, TypeError):
            y = None
        if y is None or not math.isfinite(y) or abs(y) > 40 * max(span, 1.0):
            y = None
        elif prev is not None and abs(y - prev) > 2.5 * span:       # jumps across an asymptote
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        if y is None:
            if len(cur) > 1:
                segs.append(cur)
            cur, prev = [], None
            continue
        cur.append([round(x, 4), round(y, 4)])
        prev = y
    if len(cur) > 1:
        segs.append(cur)
    return segs


def _item(raw, view: tuple[list[float], list[float]]) -> dict:
    if not isinstance(raw, dict) or raw.get("t") not in KINDS:
        raise ValueError("each thing needs t: " + ", ".join(sorted(KINDS)))
    t = raw["t"]
    out: dict = {"t": t, "c": _color(raw.get("c"))}
    label = _text(raw.get("label"), 40)
    if label:
        out["label"] = label
    if t == "axes":
        out["xr"], out["yr"] = _range(raw.get("x", view[0]), "axes x"), _range(raw.get("y", view[1]), "axes y")
        out["xl"], out["yl"] = _text(raw.get("xl", "x"), 12), _text(raw.get("yl", "y"), 12)
        out["bare"] = bool(raw.get("bare", False))        # a sketch: the space without grid, axes or numbers
    elif t == "curve":
        expr = _text(raw.get("f"), 120)
        if not expr:
            raise ValueError("curve needs f, e.g. '3*x/(x-2)'")
        lo = _num(raw["from"], "from") if "from" in raw else view[0][0]
        hi = _num(raw["to"], "to") if "to" in raw else view[0][1]
        if not lo < hi:
            raise ValueError("curve needs from below to")
        segs = sample(expr, lo, hi, view[1][0], view[1][1])
        if not segs:
            raise ValueError(f"nothing to draw for f = {expr} in that range")
        out["segs"], out["f"] = segs, expr
    elif t == "vline":
        out["x"] = _num(raw.get("x"), "x")
        out["dash"] = bool(raw.get("dash", True))
    elif t == "hline":
        out["y"] = _num(raw.get("y"), "y")
        out["dash"] = bool(raw.get("dash", True))
    elif t == "dot":
        out["x"], out["y"] = _num(raw.get("x"), "x"), _num(raw.get("y"), "y")
    elif t == "seg":
        for k in ("x1", "y1", "x2", "y2"):
            out[k] = _num(raw.get(k), k)
        out["arrow"] = bool(raw.get("arrow", False))
        out["dash"] = bool(raw.get("dash", False))
    elif t == "text":
        out["x"], out["y"] = _num(raw.get("x"), "x"), _num(raw.get("y"), "y")
        out["s"] = _text(raw.get("s"), 120)
        if not out["s"]:
            raise ValueError("text needs s")
    elif t == "poly":
        pts = raw.get("pts")
        if not isinstance(pts, list) or not 3 <= len(pts) <= 40:
            raise ValueError("poly needs 3 to 40 points [[x,y],...]")
        out["pts"] = [[_num(p[0], "pts"), _num(p[1], "pts")] for p in pts if isinstance(p, (list, tuple)) and len(p) == 2]
        if len(out["pts"]) != len(pts):
            raise ValueError("poly points must be [x, y]")
    elif t == "eq":
        lines = raw.get("lines")
        if not isinstance(lines, list) or not 1 <= len(lines) <= 8:
            raise ValueError("eq needs 1 to 8 lines")
        out["lines"] = [_text(x, 80) for x in lines]
        hi = raw.get("hi")
        out["hi"] = int(hi) if isinstance(hi, int) and not isinstance(hi, bool) and 0 <= hi < len(lines) else -1
    elif t == "table":
        head, rows = raw.get("head") or [], raw.get("rows")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 10 or not isinstance(head, list) or len(head) > 5:
            raise ValueError("table needs rows (up to 10) and head (up to 5 columns)")
        out["head"] = [_text(x, 20) for x in head]
        out["rows"] = [[_text(c, 20) for c in (r if isinstance(r, list) else [r])][:5] for r in rows]
    return out


def build(scene: dict) -> dict:
    """The scene as the app draws it. Raises ValueError with a short reason the model can act on."""
    if not isinstance(scene, dict):
        raise ValueError("scene must be an object with title and steps")
    steps = scene.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"steps must be 1 to {MAX_STEPS} items")
    view = _DEFAULT_VIEW
    for st in steps:                                   # the view is the first axes, so curves in step 1 sample over it
        for it in (st.get("do") or []) if isinstance(st, dict) else []:
            if isinstance(it, dict) and it.get("t") == "axes":
                view = (_range(it.get("x", view[0]), "axes x"), _range(it.get("y", view[1]), "axes y"))
                break
        else:
            continue
        break
    out_steps = []
    for st in steps:
        if not isinstance(st, dict):
            raise ValueError("each step is {say, do:[...]}")
        items = st.get("do") or []
        if not isinstance(items, list) or len(items) > MAX_ITEMS:
            raise ValueError(f"a step holds at most {MAX_ITEMS} things")
        out_steps.append({"say": _text(st.get("say"), 160), "do": [_item(i, view) for i in items]})
    if not any(i["t"] != "axes" for s in out_steps for i in s["do"]):
        raise ValueError("nothing to draw")
    return {"title": _text(scene.get("title"), 60) or "Misty draws", "view": {"x": view[0], "y": view[1]},
            "steps": out_steps}
