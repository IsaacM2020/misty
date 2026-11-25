"""F6: a local, opt-in collector of what the mic heard, so a few days of real talk can be labelled "for her" or "not".

Off by default (setting `collect_phrases`, said by voice: "keep what you hear", "stop keeping what you hear", "forget what you heard").
Only scrubbed text is kept, on this Mac, for 14 days. A phrase he labelled stays until he clears it. Nothing from class mode, nothing
while a private app is in front, nothing that is only a sound. No audio is ever kept or sent. The labeller (scripts/label_phrases.py)
is a thin loop over `queue`, `set_label` and `export`; the words are only ever read there, on his Mac, outside the repo.
"""
from __future__ import annotations

from pathlib import Path

from .logs import log_event
from .scrub import scrub_text

KEEP_DAYS = 14
STATES = ("listening", "speaking")
OUTCOMES = ("delivered", "dropped", "merged", "held")
LABELS = ("for_her", "not_for_her")
CAP_CHARS = 300
TARGET = 150
HER_SIDE = ("delivered", "merged", "held")          # phrases that reached her; the other side is "dropped"
CONSENT = "keeps the words of what the mic heard, on this Mac, for 14 days"


def collecting(ctx) -> bool:
    try:
        return bool(ctx.settings.get("collect_phrases"))
    except KeyError:
        return False


def set_collecting(ctx, on: bool) -> bool:
    ctx.settings.set("collect_phrases", bool(on))
    return collecting(ctx)


def add(ctx, text: str, *, state: str = "listening", named: bool = False, followup: bool = False, since_reply: float | None = None,
        outcome: str = "dropped", private: bool = False, class_mode: bool = False) -> dict:
    """Keep one heard final. Returns {kept: bool, why?}. Never raises for a bad value: a bad row is simply not kept."""
    if not collecting(ctx):
        return {"kept": False, "why": "off"}
    if class_mode:
        return {"kept": False, "why": "class"}
    if private:
        return {"kept": False, "why": "private"}
    if state not in STATES or outcome not in OUTCOMES:
        return {"kept": False, "why": "bad"}
    clean = " ".join(scrub_text(str(text or ""), "message").text.split())[:CAP_CHARS]
    if not any(c.isalpha() for c in clean):
        return {"kept": False, "why": "empty"}
    try:
        since = None if since_reply is None else round(float(since_reply), 1)
    except (TypeError, ValueError):
        since = None
    now = ctx.clock.now()
    with ctx.con:
        ctx.con.execute("INSERT INTO heard_phrases(at, text, state, named, followup, since_reply, outcome) VALUES (?,?,?,?,?,?,?)",
                        (now, clean, state, int(bool(named)), int(bool(followup)), since, outcome))
    prune(ctx)
    return {"kept": True}


def prune(ctx, now: float | None = None) -> int:
    """Unlabelled phrases older than 14 days go. A labelled one stays: it is his work."""
    cut = (now if now is not None else ctx.clock.now()) - KEEP_DAYS * 86400
    with ctx.con:
        n = ctx.con.execute("DELETE FROM heard_phrases WHERE at < ? AND label IS NULL", (cut,)).rowcount
    return n


def clear(ctx) -> int:
    """"Forget what you heard": every row, labelled or not."""
    with ctx.con:
        n = ctx.con.execute("DELETE FROM heard_phrases").rowcount
    log_event("PHRASES", code="cleared")
    return n


def stats(con) -> dict:
    rows = con.execute("SELECT outcome, label, COUNT(*) AS n FROM heard_phrases GROUP BY outcome, label").fetchall()
    out = {"kept": 0, "labelled": 0, "for_her": 0, "not_for_her": 0, "delivered_side": 0, "dropped": 0}
    for r in rows:
        out["kept"] += r["n"]
        if r["label"]:
            out["labelled"] += r["n"]
            out[r["label"]] += r["n"]
        elif r["outcome"] in HER_SIDE:
            out["delivered_side"] += r["n"]
        else:
            out["dropped"] += r["n"]
    return out


def state(ctx) -> dict:
    return {"collecting": collecting(ctx), **stats(ctx.con)}


def queue(con, limit: int = TARGET) -> list[dict]:
    """The next phrases to label, balanced: one that reached her, then one she dropped, and so on, so the set is not 150 times "not".
    Newest first inside each side. When one side runs out the rest of the other follows, so he is never stuck waiting."""
    def side(marks: str, ors: str):
        return [dict(r) for r in con.execute(f"SELECT * FROM heard_phrases WHERE label IS NULL AND outcome IN ({marks}) ORDER BY at DESC, id DESC", ors)]
    her = side(",".join("?" * len(HER_SIDE)), HER_SIDE)
    no = side("?", ("dropped",))
    out: list[dict] = []
    for i in range(max(len(her), len(no))):
        for src in (her, no):
            if i < len(src):
                out.append(src[i])
    return out[:limit]


def set_label(con, phrase_id: int, label: str, now: float) -> bool:
    if label not in LABELS:
        return False
    with con:
        n = con.execute("UPDATE heard_phrases SET label=?, labelled_at=? WHERE id=?", (label, now, int(phrase_id))).rowcount
    return n == 1


def labelled(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM heard_phrases WHERE label IS NOT NULL ORDER BY labelled_at, id")]


def export(con, path: Path) -> dict:
    """~/Misty/checks/addressed.yaml: one entry per labelled phrase, outside the repo. Returns the count per label."""
    import yaml
    rows = [{"text": r["text"], "label": r["label"], "named": bool(r["named"]), "followup": bool(r["followup"]),
             "since_reply": r["since_reply"], "outcome": r["outcome"]} for r in labelled(con)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))
    counts = {k: sum(1 for r in rows if r["label"] == k) for k in LABELS}
    return {"total": len(rows), **counts, "bar_met": len(rows) >= TARGET and counts["for_her"] >= 40 and counts["not_for_her"] >= 40}
