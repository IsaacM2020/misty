"""Cares: what Isaac says matters to him (a goal, an interest, a person). Set only by chat, one active row per key.

Facts are what Misty knows ("Prashant is my physics tutor"). Cares are what she should watch out for on his behalf
("tell me about Prashant's class changes", "cricket matters a lot"). The proactive parts of Season 3 read this list.
Nothing here knows about cricket or school: a care is a key, a label, a sentence and a weight.
"""
from __future__ import annotations

import secrets

from .errors import Fatal
from .facts import _embed, _sim, _stem, norm_key
from .logs import log_event
from .scrub import find_secrets

KINDS = ("goal", "interest", "person", "other")
LEVELS = {"high": 3, "normal": 2, "low": 1, "never": 0}
LEVEL_WORDS = {3: "matters a lot", 2: "matters", 1: "matters a little", 0: "never bring it up"}
TEXT_MAX = 300
SAME_KEY_SIM = 0.86
LIST_MAX = 40                 # cares shown to the chat model each turn


def active(con) -> list:
    return con.execute("SELECT * FROM cares WHERE status='active' ORDER BY weight DESC, updated_at DESC").fetchall()


def get(con, key: str):
    return con.execute("SELECT * FROM cares WHERE status='active' AND key=?", (norm_key(key),)).fetchone()


def same_key(ctx, key: str) -> str | None:
    """An active key that means the same as `key` ("ib math" and "ib math hl"): exact, then stem, then embedding."""
    k = norm_key(key)
    if get(ctx.con, k) is not None:
        return k
    rows = active(ctx.con)
    for r in rows:
        if _stem(r["key"]) == _stem(k):
            return r["key"]
    q = _embed(ctx, k)
    best, best_sim = None, SAME_KEY_SIM
    if q is not None:
        for r in rows:
            v = _embed(ctx, r["key"])
            s = _sim(q, v.tobytes()) if v is not None else 0.0
            if s >= best_sim:
                best, best_sim = r["key"], s
    return best


def set_care(ctx, key: str, text: str, kind: str = "other", level: str = "normal") -> dict:
    """Add a care, or change the one with the same key. Returns {action: added|updated, key, kind, weight, was}."""
    k = norm_key(key)
    text = " ".join(str(text).split())[:TEXT_MAX]
    if not k or not text:
        raise Fatal("BAD_CARE")
    if find_secrets(f"{key} {text}", "file"):
        log_event("CARE_REFUSED_SECRET", n=1)
        raise Fatal("LOOKS_SECRET")
    kind = kind if kind in KINDS else "other"
    weight = LEVELS.get(str(level).lower(), 2)
    k = same_key(ctx, k) or k
    old = get(ctx.con, k)
    now = ctx.clock.now()
    cid = "c_" + secrets.token_hex(6)
    with ctx.con:
        if old is not None:
            ctx.con.execute("UPDATE cares SET status='replaced', replaced_by=?, updated_at=? WHERE id=?",
                            (cid, now, old["id"]))
        ctx.con.execute("INSERT INTO cares(id, key, kind, text, weight, status, created_at, updated_at) "
                        "VALUES (?,?,?,?,?, 'active', ?, ?)", (cid, k, kind, text, weight, now, now))
    log_event("CARE_SAVED", n=1)
    return {"action": "updated" if old is not None else "added", "key": k, "kind": kind, "weight": weight,
            "was": line(old) if old is not None else None}


def drop(ctx, key: str) -> dict:
    """Stop caring about something that was set: the row stays as 'dropped'. {dropped: n, key}."""
    k = same_key(ctx, key) or norm_key(key)
    now = ctx.clock.now()
    with ctx.con:
        n = ctx.con.execute("UPDATE cares SET status='dropped', updated_at=? WHERE status='active' AND key=?",
                            (now, k)).rowcount
    if n:
        log_event("CARE_DROPPED", n=n)
    return {"dropped": n, "key": k}


def line(r) -> str:
    return f"- {r['key']} ({r['kind']}, {LEVEL_WORDS.get(r['weight'], 'matters')}): {r['text']}"


def block(con) -> str:
    rows = active(con)[:LIST_MAX]
    return ("What <<name>> cares about (he told you; change it only when he says so):\n"
            + ("\n".join(line(r) for r in rows) if rows else "(nothing yet)"))


def as_dict(r) -> dict:
    return {"id": r["id"], "key": r["key"], "kind": r["kind"], "text": r["text"], "weight": r["weight"],
            "updated_at": r["updated_at"]}
