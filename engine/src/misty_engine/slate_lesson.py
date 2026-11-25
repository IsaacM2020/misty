"""S10: lessons on the Slate. The old whiteboard sent a checked scene (steps of axes, curves, dots, equations) over `draw.scene`;
this turns that same scene into Slate blocks, so a lesson, a look-and-solve board and the `draw` tool all land on the Slate.
A lesson reveals step by step: the first send shows the page, later sends (a longer copy of the same scene) append only the new steps."""
from __future__ import annotations

import re

from . import slate

_WORDS = 16


def _short(s: str, n: int = _WORDS) -> str:
    w = str(s or "").split()
    return " ".join(w[:n])


def _f(expr: str) -> str:
    """'3x/(x-2)' as the checker wants it: '3*x/(x-2)'."""
    return re.sub(r"(\d|\))\s*(x|\()", r"\1*\2", str(expr))


def _prims(items: list[dict], before: list[dict]) -> list[dict]:
    """The old picture things as Slate diagram things (the page places them; only numbers in the drawing's own units)."""
    out: list[dict] = []
    ax = next((p for p in before if p["t"] == "axes"), None)
    view_x, view_y = (list(ax["x"]), list(ax["y"])) if ax else ([-10, 10], [-10, 10])
    for it in items:
        t = it.get("t")
        c = {"c": "red"} if it.get("c") in ("red", "accent", "hi") else {}
        lab = {"label": it["label"]} if it.get("label") else {}
        if t == "axes":
            view_x, view_y = list(it.get("xr") or view_x), list(it.get("yr") or view_y)
            out.append({"t": "axes", "x": view_x, "y": view_y, "xl": it.get("xl", "x"), "yl": it.get("yl", "y")})
        elif t == "curve":
            q = {"t": "plot", "f": _f(it["f"]), **c, **lab}
            out.append(q)
        elif t == "dot":
            out.append({"t": "point", "x": it["x"], "y": it["y"], **c, **lab})
        elif t == "seg":
            if it.get("arrow"):
                out.append({"t": "arrow", "x1": it["x1"], "y1": it["y1"], "x2": it["x2"], "y2": it["y2"], **c, **lab})
            else:
                out.append({"t": "line", "x1": it["x1"], "y1": it["y1"], "x2": it["x2"], "y2": it["y2"],
                            **({"dashed": True} if it.get("dash") else {}), **c, **lab})
        elif t == "vline":
            out.append({"t": "line", "x1": it["x"], "y1": view_y[0], "x2": it["x"], "y2": view_y[1], "dashed": bool(it.get("dash", True)), **c, **lab})
        elif t == "hline":
            out.append({"t": "line", "x1": view_x[0], "y1": it["y"], "x2": view_x[1], "y2": it["y"], "dashed": bool(it.get("dash", True)), **c, **lab})
        elif t == "text":
            out.append({"t": "label", "x": it["x"], "y": it["y"], "s": it["s"]})
        elif t == "poly":
            pts = it["pts"]
            for a, b in zip(pts, pts[1:] + pts[:1]):
                out.append({"t": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1], **c})
    return out


def step_blocks(step: dict, shown: list[dict], ask: bool) -> tuple[list[dict], list[dict]]:
    """Blocks for one scene step, and the diagram things drawn so far (the picture is cumulative, as the old board's was)."""
    say = str(step.get("say") or "").strip()
    items = [i for i in step.get("do") or [] if isinstance(i, dict)]
    blocks: list[dict] = []
    new = _prims([i for i in items if i.get("t") not in ("eq", "table")], shown)
    if new:
        fresh = shown + new
        if len(fresh) > slate.MAX_PRIMS:                                 # keep the axes and the newest things
            axes = [p for p in fresh if p["t"] == "axes"][:1]
            fresh = axes + [p for p in fresh if p["t"] != "axes"][-(slate.MAX_PRIMS - len(axes)):]
        shown = fresh
        blocks.append({"k": "diagram", "items": shown})
    for it in items:
        if it.get("t") == "eq":
            for line in it.get("lines") or []:
                blocks.append({"k": "math", "tex": str(line)})
        elif it.get("t") == "table":
            for row in it.get("rows") or []:
                blocks.append({"k": "text", "text": _short(" , ".join(str(c) for c in row))})
    if not blocks:
        blocks.append({"k": "text", "text": _short(say) or "Next"})
    blocks[0]["say"] = say
    if ask:
        q = re.split(r"(?<=[.?!])\s+", say)[-1] if say else "Your turn"
        blocks.append({"k": "turn", "prompt": _short(q, 8) or "Your turn", "say": ""})
    return blocks, shown


def _safe(raw_blocks: list[dict], title: str, final: bool) -> dict:
    """slate.build over the blocks; a block that does not build is dropped, and a whole failure is a plain text page."""
    for _ in range(len(raw_blocks) + 1):
        try:
            page = slate.build({"title": title, "blocks": raw_blocks[:slate.MAX_BLOCKS]})
            break
        except ValueError:
            raw_blocks = [b for b in raw_blocks if b["k"] != "math"] or raw_blocks[:1]
    else:
        page = slate.build({"title": title, "blocks": [{"k": "text", "text": _short(title) or "Lesson", "say": ""}]})
    if not final:
        for b in page["blocks"]:
            b.pop("mark", None)
    return page


async def show(ctx, scene: dict, asks: list[bool] | None = None, *, final: bool | None = None) -> None:
    """Put the scene (whole, or a longer copy of one already shown) on the Slate."""
    steps = [s for s in scene.get("steps") or [] if isinstance(s, dict)]
    if not steps or ctx.notify is None:
        return
    title = str(scene.get("title") or "")
    asks = asks or []
    if final is None:
        final = len(steps) >= len(asks) if asks else True
    st = getattr(ctx, "slate_lesson", None)
    cont = (st is not None and st["title"] == title and 0 < st["shown"] < len(steps) and ctx.slate_page is st["page"])
    start = st["shown"] if cont else 0
    prims = st["prims"] if cont else []
    raw: list[dict] = []
    for i in range(start, len(steps)):
        bl, prims = step_blocks(steps[i], prims, bool(asks[i]) if i < len(asks) else False)
        raw += bl
    if cont:
        page = _safe(raw, title, final)
        base = len(ctx.slate_page["blocks"])
        ctx.slate_page["blocks"] += page["blocks"]
        ctx.slate_page["kind"] = "stay"
        slate.resave(ctx, ctx.slate_page)
        await ctx.notify("slate.append", {"blocks": page["blocks"], "from": base})
    else:
        page = _safe(raw, title, final)
        page["kind"] = "stay"
        await ctx.notify("slate.show", page)
        slate.hold(ctx, page, ctx.clock.now())
    ctx.slate_lesson = {"title": title, "shown": len(steps), "prims": prims, "page": ctx.slate_page}
