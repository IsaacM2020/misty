"""The Slate's page format and its checker (S1 `math`, S2 every block kind).

The model sends blocks, never coordinates and never a sum it worked out itself. Numbers come from `calc`
(`\\ans`, `check` on plain sums); algebra and calculus lines are checked with sympy. A block that fails is dropped
with a reason; a failed answer kills the whole slate and she answers by voice. `kind` is worked out here."""
from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _Timeout

MAX_BLOCKS = 12
MAX_TEX = 200
MAX_SAY = 240
MAX_WORDS = 22          # a `text` or `note` line: the model is told 16, a few over is kept rather than lost
MAX_STEPS = 8
MAX_STEP_WORDS = 12
MAX_PRIMS = 14
ASK = "Everything look right?"
PENDING_SECONDS = 900   # a read-back nobody answers is forgotten after 15 minutes; the slate itself stays on screen
HOLD_SECONDS = 40       # "wait" holds a quick slate only while it is still on screen
MARKS = {"under", "circle", "double"}
KINDS = {"text", "math", "steps", "diagram", "turn", "note"}
MARKABLE = {"text", "math", "steps", "diagram"}

# diagram primitives: name -> (required keys, optional keys). Numbers are in the diagram's own units (the axes),
# never pixels; the engine turns them into the 0 to 1000 grid (S4).
PRIMS = {
    "axes": ({"x", "y"}, {"xl", "yl"}),
    "plot": ({"f"}, {"from", "to", "c", "label"}),
    "point": ({"x", "y"}, {"label", "c"}),
    "line": ({"x1", "y1", "x2", "y2"}, {"dashed", "label", "c"}),
    "arrow": ({"x1", "y1", "x2", "y2"}, {"label", "c"}),
    "vector": ({"x1", "y1", "x2", "y2"}, {"label", "c"}),
    "circle": ({"x", "y", "r"}, {"label", "c"}),
    "angle": ({"x", "y", "from", "to"}, {"label", "c"}),
    "label": ({"x", "y", "s"}, set()),
    "atom": ({"id", "el"}, set()),
    "bond": ({"a", "b"}, {"n"}),
    "lone_pair": ({"at"}, {"n"}),
    "molecule": ({"formula"}, {"charge"}),
    "curve_pair": ({"kind"}, {"labels"}),
    "box": ({"s"}, {"id", "to"}),
}
TEXT_KEYS = {"label", "xl", "yl", "s", "c", "el", "id", "a", "b", "at", "kind", "formula", "f", "to_box"}
FREE_KEYS = {"labels", "dashed"}
# keys that mean the model tried to place things on the page itself
COORD_KEYS = {"d", "path", "svg", "pts", "points", "px", "py", "left", "top", "width", "height", "pos", "position",
              "coords", "coordinates", "cx", "cy", "html"}


def _text(v, n: int) -> str:
    return str(v if v is not None else "").strip()[:n]


def fmt(v) -> str:
    """25 not 25.0; no 0.30000000000000004."""
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError("answer is not a finite number")
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.10g}"
    return str(v)


def words(s: str) -> int:
    """Words in a line; one `$...$` stretch of maths counts as one word."""
    return len(re.sub(r"\$[^$]*\$", " m ", s).split())


# ---------------------------------------------------------------- the check (numbers: calc, algebra: sympy)

_ALLOWED = {"x", "y", "z", "t", "n", "k", "a", "b", "c", "pi", "e", "E", "oo", "sin", "cos", "tan", "exp", "log", "ln",
            "sqrt", "diff", "integrate", "Abs", "abs", "asin", "acos", "atan", "sinh", "cosh", "tanh", "sec", "csc", "cot"}
_POINTS = (0.3, 0.7, 1.3, 1.9, 2.6)


def _sympy_equal(lhs: str, rhs: str) -> bool:
    import sympy as sp
    from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication_application, parse_expr,
                                            standard_transformations)
    for s in (lhs, rhs):
        if len(s) > 200 or "__" in s or re.search(r"[\"'`;\\\[\]{}]", s):
            raise ValueError("not a formula I can read")
        bad = set(re.findall(r"[A-Za-z_]\w*", s)) - _ALLOWED
        if bad:
            raise ValueError(f"unknown name {sorted(bad)[0]!r}")
    local = {n: getattr(sp, n) for n in ("sin", "cos", "tan", "exp", "log", "sqrt", "diff", "integrate", "Abs", "asin",
                                         "acos", "atan", "sinh", "cosh", "tanh", "sec", "csc", "cot", "pi", "E", "oo")}
    local.update(ln=sp.log, abs=sp.Abs, e=sp.E)
    for v in "xyztnkabc":
        local[v] = sp.Symbol(v)
    glob = {"__builtins__": {}, "Integer": sp.Integer, "Float": sp.Float, "Rational": sp.Rational, "Symbol": sp.Symbol,
            "Function": sp.Function}
    tr = standard_transformations + (convert_xor, implicit_multiplication_application)
    try:
        a = parse_expr(lhs, local_dict=local, global_dict=glob, transformations=tr)
        b = parse_expr(rhs, local_dict=local, global_dict=glob, transformations=tr)
    except Exception:
        raise ValueError("not a formula I can read")
    syms = sorted((a.free_symbols | b.free_symbols), key=str)
    good = 0
    for k in range(len(_POINTS) if syms else 1):
        sub = {s: _POINTS[(k + i) % len(_POINTS)] for i, s in enumerate(syms)}
        try:
            da, db = complex(a.subs(sub).evalf()), complex(b.subs(sub).evalf())
        except Exception:
            continue
        if not all(map(math.isfinite, (da.real, da.imag, db.real, db.imag))):
            continue
        if abs(da - db) > 1e-7 * max(1.0, abs(da), abs(db)):
            return False
        good += 1
    if good >= (3 if syms else 1):
        return True
    return bool(sp.simplify(a - b) == 0)                    # too few usable sample points: try it symbolically


def check_equal(lhs: str, rhs: str) -> bool:
    """True when both sides are equal. Plain numbers go through `calc` (sin is in degrees there); anything with a
    letter in it goes to sympy (radians) with 5 sample points. Raises ValueError when a side can't be read."""
    from .chat import calc
    lhs, rhs = _text(lhs, 200), _text(rhs, 200)
    if not lhs or not rhs:
        raise ValueError("check needs lhs and rhs")
    try:
        a, b = calc(lhs), calc(rhs)
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    except ValueError:
        pass
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(_sympy_equal, lhs, rhs).result(timeout=4)
    except _Timeout:
        raise ValueError("too slow to check")
    finally:
        pool.shutdown(wait=False)


# ---------------------------------------------------------------- blocks

def build(raw) -> dict:
    """The clean slate the app draws, or ValueError with a short reason."""
    from .chat import calc
    if not isinstance(raw, dict):
        raise ValueError("slate must be an object with blocks")
    blocks_in = raw.get("blocks")
    if not isinstance(blocks_in, list) or not blocks_in:
        raise ValueError("slate needs a list of blocks")
    if len(blocks_in) > MAX_BLOCKS:
        raise ValueError(f"at most {MAX_BLOCKS} blocks")
    blocks_in = _split_long(blocks_in)[:MAX_BLOCKS]
    out, dropped, lost_pictures = [], [], []
    for i, b in enumerate(blocks_in):
        try:
            out.append(_block(b, calc, bool(out), next((x["chem"] for x in reversed(out) if x.get("chem")), None)))
        except ValueError as e:
            is_answer = isinstance(b, dict) and (isinstance(b.get("ans"), dict)
                                                 or (i == len(blocks_in) - 1 and b.get("check") is not None))
            if is_answer:
                raise ValueError(f"the answer block failed: {e}")      # a failed answer kills the slate
            dropped.append(f"block {i}: {e}")
            if isinstance(b, dict) and b.get("k") == "diagram":
                lost_pictures.append(f"block {i}: {e}")
    # A picture that could not be drawn fails the slate, so she fixes it and calls again instead of saying "drawn" over
    # a page with no picture (17 Nov 2026 live test: two diagrams dropped, both times she said it was drawn).
    if lost_pictures:
        raise ValueError("the diagram failed: " + "; ".join(lost_pictures))
    if not out:
        raise ValueError("no block could be built")
    out = _guard_chem(out, dropped)
    target = next((blk for blk in reversed(out) if blk["k"] in MARKABLE), None)
    if target is None:
        raise ValueError("no answer to mark")
    target["mark"] = target.get("mark") or "under"                      # every slate ends on a marked answer
    stay = len(out) > 3 or any(blk["k"] in ("steps", "turn") for blk in out)
    page = {"title": _text(raw.get("title"), 60), "kind": "stay" if stay else "quick", "blocks": out, "dropped": dropped}
    if any(blk["k"] == "steps" and blk.get("confirm") for blk in out):
        page["kind"], page["ask"] = "stay", ASK                           # a protocol is read back and waits for his word
    return page


_GEO_RE = re.compile(r"\b(" + "|".join(sorted(__import__("misty_engine.chem", fromlist=["NAMES"]).NAMES, key=len, reverse=True)) + r")\b", re.I)
_TOK = re.compile(r"\\(geom|domains|ve|bonds|lps|pairs|angle)(?![A-Za-z])")


def _strings(blk):
    for key in ("tex", "text", "prompt"):
        if isinstance(blk.get(key), str):
            yield blk[key]
    yield from (blk.get("items") or []) if blk["k"] == "steps" else []
    say = blk.get("say")
    yield from (say if isinstance(say, list) else [say or ""])


def _guard_chem(out: list, dropped: list) -> list:
    """A line that names a shape the structure does not have is wrong: the geometry name comes from chem.py, not the model."""
    ctxs = [b.get("chem") for b in out]
    last = max((i for i, b in enumerate(out) if b["k"] in MARKABLE), default=-1)
    keep = []
    for i, blk in enumerate(out):
        ctx = next((c for c in reversed(ctxs[:i + 1]) if c), None) or next((c for c in ctxs[i:] if c), None)
        if ctx and blk["k"] != "diagram":
            ok = {ctx["geometry"], ctx["domains"], *[g.strip() for g in ctx["geometry"].split(",")]}
            for s in _strings(blk):
                named = [m.lower() for m in _GEO_RE.findall(s)]
                if any(m in ok for m in named):
                    continue                    # it names the right shape too: a comparison ("four give tetrahedral, five ..."), not a wrong claim
                bad = next((m for m in named if m not in ok), None)
                if bad:
                    if blk.get("ans") or blk.get("mark") or i == last:
                        raise ValueError(f"the answer block failed: says {bad} but {ctx['formula']} is {ctx['geometry']}")
                    dropped.append(f"block {i}: says {bad} but {ctx['formula']} is {ctx['geometry']}")
                    break
            else:
                keep.append(blk)
            continue
        keep.append(blk)
    return keep


def _fill(blk: dict, ctx) -> None:
    """\\geom, \\ve, \\bonds, \\lps, \\pairs, \\domains, \\angle in a line are replaced by the molecule's facts, from code."""
    from . import chem

    def sub(tex: bool):
        def f(m):
            if not ctx:
                raise ValueError(f"\\{m.group(1)} needs a molecule diagram above it")
            return chem.fact_tex(ctx, m.group(1)) if tex else chem.fact(ctx, m.group(1))
        return f
    for key in ("tex", "text", "prompt"):
        if isinstance(blk.get(key), str) and _TOK.search(blk[key]):
            blk[key] = _TOK.sub(sub(key == "tex"), blk[key])
    if blk["k"] == "steps":
        blk["items"] = [_TOK.sub(sub(False), t) for t in blk["items"]]
    say = blk.get("say")
    if isinstance(say, list):
        blk["say"] = [_TOK.sub(sub(False), t) for t in say]
    elif isinstance(say, str):
        blk["say"] = _TOK.sub(sub(False), say)


def _block(b, calc, has_above: bool, ctx=None) -> dict:
    if not isinstance(b, dict):
        raise ValueError("a block must be an object")
    k = b.get("k")
    if k not in KINDS:
        raise ValueError(f"unknown block kind {k!r}")
    bad = COORD_KEYS & set(b)
    if bad:
        raise ValueError(f"coordinates sent ({sorted(bad)[0]}): the page places things, not the model")
    blk = {"k": k, "say": _say(b.get("say"))}
    if b.get("mark") in MARKS and k in MARKABLE:
        blk["mark"] = b["mark"]
    if k == "math":
        _math(b, blk, calc, ctx)
    elif k in ("text", "note"):
        if k == "note" and not has_above:
            raise ValueError("a note needs a block above it")
        blk["text"] = _line(b.get("text"), MAX_WORDS)
    elif k == "steps":
        _steps(b, blk)
    elif k == "diagram":
        _diagram(b, blk, calc)
    elif k == "turn" and b.get("prompt"):
        blk["prompt"] = _line(b.get("prompt"), 8)
    _fill(blk, ctx)
    return blk


def _say(v):
    if isinstance(v, list):
        return [_text(s, MAX_SAY) for s in v[:MAX_STEPS]]
    return _text(v, MAX_SAY)


def _line(v, max_words: int) -> str:
    s = _text(v, 300)
    if not s:
        raise ValueError("empty line")
    if words(s) > max_words:
        raise ValueError(f"more than {max_words} words: short key lines only, never a paragraph")
    return s


def _math(b, blk, calc, ctx=None) -> None:
    tex = _text(b.get("tex"), MAX_TEX)
    if not tex:
        raise ValueError("math needs tex")
    blk["tex"] = tex
    ans = b.get("ans")
    if isinstance(ans, dict) and "chem" in ans:                        # the answer is a fact about the molecule above
        from . import chem
        if "\\ans" not in tex:
            raise ValueError("\\ans and ans go together")
        if not ctx:
            raise ValueError("ans.chem needs a molecule diagram above it")
        blk["ans"] = {"expr": "chem:" + str(ans["chem"]), "value": chem.fact_tex(ctx, str(ans["chem"]))}
    elif "\\ans" in tex or ans is not None:
        if not isinstance(ans, dict) or "\\ans" not in tex:
            raise ValueError("\\ans and ans.expr go together")
        expr = _text(ans.get("expr"), 300)
        try:
            value = fmt(calc(expr))
        except ValueError as e:
            raise ValueError(f"can't work out {expr!r}: {e}")
        blk["ans"] = {"expr": expr, "value": value}
    chk = b.get("check")
    if chk is not None:
        if not isinstance(chk, dict):
            raise ValueError("check must be {lhs, rhs}")
        try:
            ok = check_equal(chk.get("lhs"), chk.get("rhs"))
        except ValueError as e:
            raise ValueError(f"can't check this line: {e}")
        if not ok:
            raise ValueError(f"the two sides are not equal ({_text(chk.get('lhs'), 60)} vs {_text(chk.get('rhs'), 60)})")
        blk["check"] = {"lhs": _text(chk.get("lhs"), 200), "rhs": _text(chk.get("rhs"), 200)}


def _steps(b, blk) -> None:
    items = b.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("steps needs a list of items")
    if len(items) > MAX_STEPS:
        raise ValueError(f"at most {MAX_STEPS} steps")
    blk["items"] = [_line(s, MAX_STEP_WORDS) for s in items]
    if b.get("confirm") is True:
        blk["confirm"] = True
    if isinstance(blk["say"], list):
        blk["say"] = blk["say"][:len(items)]


_SD = {"supply": 0, "s": 0, "demand": 1, "d": 1}
_FORMULA = re.compile(r"\b[A-Z][A-Za-z0-9()]*(?:\s?[+-]|\^?\d?[+-])?")


def _formula_in(p: dict) -> str | None:
    """The formula a molecule means: its `formula`, else the first formula-looking word under any key the model used
    ("SF4", "CH4: four bonding domains", "SF4 seesaw: ...") that chem can actually draw."""
    from . import chem
    for k in ("formula", "f", "name", "molecule", "text", "s", "label"):
        v = p.get(k)
        if not isinstance(v, str) or not v.strip():
            continue
        if k == "formula":
            return v.strip()
        for m in _FORMULA.finditer(v):
            tok = m.group(0).replace(" ", "")
            try:
                chem.lewis(tok, p.get("charge", 0))
                return tok
            except (ValueError, KeyError):
                continue
    return None


def _repair(prims: list) -> list:
    """Fix what models get wrong most, instead of dropping the picture (17 Nov 2026 live test):
    axes named instead of ranged (x="Quantity") and supply / demand sent as plots of words."""
    if not all(isinstance(p, dict) for p in prims):
        return prims                                      # the loop below reports what is wrong
    sd = [p for p in prims if p.get("t") == "plot" and str(p.get("f", "")).strip().lower() in _SD]
    if sd and not any(p.get("t") == "curve_pair" for p in prims):
        labels = ["", ""]
        for p in sd:
            i = _SD[str(p["f"]).strip().lower()]
            labels[i] = str(p.get("label") or ("S" if i == 0 else "D"))
        rest = [p for p in prims if p not in sd and p.get("t") != "axes"]
        return [{"t": "curve_pair", "kind": "supply_demand", "labels": [labels[0] or "S", labels[1] or "D"]}] + rest
    fixed = []
    for p in prims:
        if p.get("t") == "axes" and (isinstance(p.get("x"), str) or isinstance(p.get("y"), str)):
            p = dict(p)
            for k in ("x", "y"):
                if isinstance(p.get(k), str):
                    p.setdefault(k + "l", p[k])
                    p[k] = [0, 10]
        fixed.append(p)
    return fixed


def _split_long(blocks: list) -> list:
    """A text or note line longer than MAX_WORDS becomes two or more lines (at a sentence, colon or comma when it can)
    instead of being dropped. The spoken `say` stays on the first; the mark moves to the last."""
    out = []
    for b in blocks:
        if not (isinstance(b, dict) and b.get("k") in ("text", "note") and isinstance(b.get("text"), str)
                and words(b["text"]) > MAX_WORDS):
            out.append(b)
            continue
        parts, cur = [], ""
        for piece in re.split(r"(?<=[.:;,!?])\s+", b["text"].strip()):
            cand = (cur + " " + piece).strip()
            if cur and words(cand) > MAX_STEP_WORDS + 4:
                parts.append(cur)
                cur = piece
            else:
                cur = cand
        if cur:
            parts.append(cur)
        lines = []
        for s in parts:                                   # a piece with no breaks at all is cut by words
            w = s.split()
            lines += [" ".join(w[j:j + 16]) for j in range(0, len(w), 16)]
        for j, line in enumerate(lines):
            nb = {**b, "text": line, "k": b["k"] if j == 0 else "text"}
            if j:
                nb["say"] = ""
            if j < len(lines) - 1:
                nb.pop("mark", None)
            out.append(nb)
    return out


def _diagram(b, blk, calc) -> None:
    prims = b.get("items")
    if not isinstance(prims, list) or not prims:
        raise ValueError("diagram needs a list of items")
    if len(prims) > MAX_PRIMS:
        raise ValueError(f"at most {MAX_PRIMS} things in a diagram")
    prims = _repair(prims)
    out = []
    for p in prims:
        t = p.get("t") if isinstance(p, dict) else None
        if t not in PRIMS:
            raise ValueError(f"unknown diagram thing {t!r}")
        if t in ("atom", "bond", "lone_pair"):
            raise ValueError(f"{t}: draw a molecule from its formula, the engine places the atoms")
        if COORD_KEYS & set(p):
            raise ValueError(f"coordinates sent ({sorted(COORD_KEYS & set(p))[0]}): the page places things, not the model")
        req, opt = PRIMS[t]
        if t == "molecule":                              # models often name the key differently, or only label it
            p = {**p, "formula": _formula_in(p)} if _formula_in(p) else p
        if req - set(p):
            raise ValueError(f"{t} needs {sorted(req - set(p))[0]}")
        q = {"t": t, **{key: p[key] for key in (req | opt) if key in p}}
        for key, v in q.items():
            if key == "t" or key in FREE_KEYS:
                continue
            if t == "axes" and key in ("x", "y"):
                if not (isinstance(v, list) and len(v) == 2 and all(_num(n) for n in v) and v[0] < v[1]):
                    raise ValueError(f"axes {key} must be [low, high]")
            elif key in TEXT_KEYS or (t == "box" and key == "to") or (t == "molecule" and key == "charge" and isinstance(v, str)):
                if isinstance(v, (dict, list)):
                    raise ValueError(f"{key} must be text")
                q[key] = _text(v, 120)
            elif not _num(v):
                raise ValueError(f"{t} {key} must be a number")
        if q.get("c") != "red":
            q.pop("c", None)                                    # the one accent colour: red marks what carries the meaning
        if t == "plot":
            f, lo, hi = q["f"], q.get("from", -5), q.get("to", 5)
            if not any(_ok(calc, f, v) for v in (lo, hi, (lo + hi) / 2, 1.0, 0.5)):
                raise ValueError(f"can't plot {f!r}")
        if t == "molecule":
            from . import chem
            if len(prims) > 1:
                raise ValueError("a molecule goes in a diagram of its own")
            try:
                info = chem.lewis(q["formula"], q.get("charge", 0))
            except ValueError as e:
                raise ValueError(f"molecule {q['formula']}: {e}")
            q.update(formula=info["formula"], charge=info["charge"], info=info)
            blk["chem"] = chem.summary(info)
        out.append(q)
    from . import draw, slate_geo
    geo = slate_geo.layout(out, draw.sample)
    for q in out:
        q.pop("info", None)
    blk["items"] = out
    if geo:
        blk["geo"] = geo


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= 1e4


def _ok(calc, f, x) -> bool:
    try:
        calc(f, {"x": x})
        return True
    except ValueError:
        return False


# ---- S8: the read-back. After a protocol the engine holds the slate until Isaac answers. No model is asked for ok / change.
_OK = re.compile(r"^(ok(ay)?|yes|yeah|yep|yup|sure|good|fine|perfect|great|done|"
                 r"(that'?s|it'?s|all|looks?|sounds?|that looks?|that sounds?|this looks?)( (all|really))? (good|fine|right|great|perfect|correct)|"
                 r"looks? good|all good|sounds good|that'?s? all)( to me)?[.!\s]*$", re.I)
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8}
_CHANGE = re.compile(r"^(?:please )?(?:change|make|set|replace|update|swap)\s+(?:the )?(?:step |item |number |no\.? |#)?(\d|" + "|".join(_WORDS) +
                     r")(?:st|nd|rd|th)?(?: step| item| one)?\s+(?:to|into|with|as|say)\s+(.+?)[.!\s]*$", re.I)
_WHICH = re.compile(r"^(?:i )?(?:want to |wanna |need to |would like to |let'?s )?(?:change|edit|fix|adjust)(?: something| one| a step| it| that)?[.!\s]*$"
                    r"|^change something[.!\s]*$|^not quite[.!\s]*$|^no[.!\s]*$|^nope[.!\s]*$", re.I)
_WAIT = re.compile(r"^(wait|hold on|hang on|hold it|wait a (sec|second|moment|minute)|one (sec|second|moment))[.!\s]*$", re.I)


def pending_steps(page: dict):
    """(index, block) of the steps block a read-back is about."""
    return next(((i, b) for i, b in enumerate(page["blocks"]) if b["k"] == "steps" and b.get("confirm")), (None, None))


def hold(ctx, page: dict, now: float) -> None:
    """A slate just opened: remember it so a read-back, or a 'wait' on a quick one, can find it."""
    ctx.slate_page, ctx.slate_at = page, now
    ctx.slate_id = save(ctx, page)
    ctx.slate_pending = now if page.get("ask") else None


def classify(ctx, text: str, now: float) -> tuple | None:
    """What a word means to the open slate: ('ok',), ('which',), ('change', n, text), ('wait',) or None (a normal turn)."""
    page, t = getattr(ctx, "slate_page", None), text.strip()
    if page is None:
        return None
    pend = getattr(ctx, "slate_pending", None)
    if pend is not None and now - pend > PENDING_SECONDS:
        ctx.slate_pending = pend = None                                   # timeout: nobody answered, the slate stays, a normal turn
    if pend is None:
        if page["kind"] == "quick" and now - ctx.slate_at <= HOLD_SECONDS and _WAIT.match(t):
            return ("wait",)
        return None
    _, blk = pending_steps(page)
    if _OK.match(t):
        return ("ok",)
    m = _CHANGE.match(t)
    if m:
        n = _WORDS.get(m.group(1).lower()) or int(m.group(1))
        return ("change", n, m.group(2).strip()) if 1 <= n <= len(blk["items"]) else ("badn", n)
    if _WHICH.match(t):
        return ("which",)
    return None


def change_item(page: dict, n: int, text: str) -> str:
    """Step n becomes `text` (same short-line rules as any step); returns the line she says back."""
    i, blk = pending_steps(page)
    line = _line(text, MAX_STEP_WORDS)
    blk["items"][n - 1] = line
    if isinstance(blk.get("say"), list) and n - 1 < len(blk["say"]):
        blk["say"][n - 1] = line
    return line


async def answer(ctx, text: str, now: float) -> str | None:
    """Run the open slate's read-back for one of his words. Returns what she says, or None when it is a normal turn."""
    c = classify(ctx, text, now)
    if c is None:
        return None
    emit = ctx.notify
    if c[0] == "wait":
        ctx.slate_at = now
        if emit:
            await emit("slate.hold", {})
        return "Sure."
    if c[0] == "ok":
        page = ctx.slate_page
        ctx.slate_pending = None
        ctx.slate_saved = page                                            # S9 turns this into a saved row
        if emit:
            await emit("slate.close", {"saved": True})
        return "Done."
    if c[0] == "which":
        return "Of course. Which step, and what should it say?"
    if c[0] == "badn":
        return f"There is no step {c[1]}. Which one?"
    n, new = c[1], c[2]
    try:
        line = change_item(ctx.slate_page, n, new)
    except ValueError:
        return "Keep it short, a few words. What should that step say?"
    ctx.slate_pending = now
    resave(ctx, ctx.slate_page)
    if emit:
        await emit("slate.update", {"block": pending_steps(ctx.slate_page)[0], "item": n - 1, "text": line})
    return f"Changed step {n}. {ASK}"


# ---- S9: history. The page JSON is kept exactly as sent, so a reopened slate is identical.
import json as _json


def save(ctx, page: dict) -> int:
    with ctx.con:
        cur = ctx.con.execute("INSERT INTO slates(turn, title, kind, json, created_at) VALUES (?,?,?,?,?)",
                              (getattr(ctx, "turn", "") or "", page.get("title", ""), page.get("kind", "quick"),
                               _json.dumps(page, ensure_ascii=False), ctx.clock.now()))
    return int(cur.lastrowid)


def resave(ctx, page: dict) -> None:
    """A change to step N rewrites the saved slate, so history shows the final version."""
    if getattr(ctx, "slate_id", 0):
        with ctx.con:
            ctx.con.execute("UPDATE slates SET json=? WHERE id=?", (_json.dumps(page, ensure_ascii=False), ctx.slate_id))


def listing(ctx, limit: int = 100) -> list[dict]:
    rows = ctx.con.execute("SELECT id, turn, title, kind, created_at FROM slates ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r[0], "turn": r[1], "title": r[2], "kind": r[3], "created_at": r[4]} for r in rows]


def get(ctx, sid: int) -> dict | None:
    r = ctx.con.execute("SELECT json FROM slates WHERE id=?", (sid,)).fetchone()
    return _json.loads(r[0]) if r else None
