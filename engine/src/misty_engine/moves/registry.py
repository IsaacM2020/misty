"""The move registry and the one way to run a move: look up, check params, repeat-safe, risk, ask, run, verify, log.

Rules (D1): params are checked in code and the error says what to fix; the same call_id runs once; `never` is refused; an
`ask` move shows its exact preview and waits (silence is a no); a move is only "verified" when its own read-back says so;
every call is logged; text she read can't quietly trigger an `ask` move (the preview says where it came from)."""
from __future__ import annotations

import json
import asyncio
from typing import Awaitable, Callable

from ..logs import log_event
from ..scrub import find_secrets, scrub_text
from .base import Move, Outcome, Param

MOVES: dict[str, Move] = {}
Confirm = Callable[[str], Awaitable[bool]]
CONFIRM_SECONDS = 60.0         # D3: no yes within a minute is a no (the answer is logged as `timeout`)


def register(m: Move) -> Move:
    MOVES[m.name] = m
    return m


def catalog() -> str:
    """One line per move for the tool description: name, params, risk."""
    out = []
    for m in MOVES.values():
        ps = ", ".join(f"{k}{'?' if not p.required else ''}" for k, p in m.params.items())
        out.append(f"{m.name}({ps}) [{m.risk}]: {m.doc}")
    return "\n".join(out)


def pick_lines() -> list[dict]:
    """The moves' own tool-pick lines as check rows (R2). Tag = the move's name, so `toolpick.py --only open_app` runs them."""
    rows: list[dict] = []
    for m in MOVES.values():
        for say in m.says:
            rows.append({"say": say, "expect_tool": "move", "expect_args_contains": m.name, "_src": "moves", "_tags": {m.name.lower()}})
        for say, tool in m.not_mine:
            rows.append({"say": say, "expect_tool": tool, "_src": "moves", "_tags": {m.name.lower()}})
    return rows


def check_params(m: Move, raw) -> tuple[dict | None, str]:
    """(clean params, "") or (None, a precise error the model can fix)."""
    if not isinstance(raw, dict):
        return None, f"params must be an object with: {', '.join(m.params) or 'nothing'}"
    extra = sorted(set(raw) - set(m.params))
    if extra:
        return None, f"{m.name} has no param {', '.join(extra)}; it takes {', '.join(m.params) or 'nothing'}"
    clean: dict = {}
    for k, p in m.params.items():
        if raw.get(k) is None or raw.get(k) == "":
            if p.required:
                return None, f"{m.name} needs {k}" + (f" ({p.doc})" if p.doc else "")
            continue
        v = raw[k]
        if p.kind is int:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
                return None, f"{k} must be a whole number"
            v = int(v)
            if (p.lo is not None and v < p.lo) or (p.hi is not None and v > p.hi):
                return None, f"{k} must be between {p.lo} and {p.hi}"
        elif p.kind is list:
            if not isinstance(v, list) or not v or not all(isinstance(i, dict) for i in v):
                return None, f"{k} must be a non-empty list of objects"
            if p.hi is not None and len(v) > p.hi:
                return None, f"{k} has too many items (at most {p.hi})"
            for item in v:
                for kk, vv in item.items():
                    if not isinstance(vv, (str, int, float)) or isinstance(vv, bool) or len(str(vv)) > p.max_len:
                        return None, f"every {k} item must hold only short text or numbers"
                    if isinstance(vv, str) and find_secrets(vv):
                        return None, f"{k} looks like it holds a password or key: moves never carry those"
        elif p.kind is bool:
            if not isinstance(v, bool):
                return None, f"{k} must be true or false"
        else:
            if not isinstance(v, str):
                return None, f"{k} must be text"
            v = v.strip()
            if len(v) > p.max_len:
                return None, f"{k} is too long (at most {p.max_len} characters)"
            if any(find_secrets(piece) for piece in v.split("/")):          # a long path is not a key: look piece by piece
                return None, f"{k} looks like a password or key: moves never carry those"
            if p.choices and v not in p.choices:
                return None, f"{k} must be one of: {', '.join(p.choices)}"
        clean[k] = v
    return clean, ""


def _log(ctx, call_id, name, params, risk, asked, answer, verified, what, undo, error, source, protocol="") -> None:
    safe = scrub_text(json.dumps(params, default=str)).text[:1000]
    with ctx.con:
        ctx.con.execute("INSERT OR REPLACE INTO moves_log(at, call_id, name, params, risk, asked, answer, verified, what, undo, error, source, protocol) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ctx.clock.now(), call_id, name, safe, risk, int(asked), answer,
                         None if verified is None else int(verified), what[:300], json.dumps(undo) if undo else "", error[:200], source, protocol))


def _result(o: Outcome, **more) -> dict:
    return {"ok": o.ok, "verified": o.verified, "what": o.what, **({"undo_available": True} if o.undo else {}), **o.data, **more}


async def run(ctx, name: str, params, call_id: str, confirm: Confirm | None = None, source: str = "him", protocol: str = "") -> dict:
    m = MOVES.get(str(name or ""))
    if m is None:
        return {"ok": False, "verified": False, "error": f"no move called {name}. The moves are: {', '.join(MOVES)}"}
    call_id = str(call_id or "")[:64]
    if call_id:
        row = ctx.con.execute("SELECT * FROM moves_log WHERE call_id=?", (call_id,)).fetchone()
        if row is not None:                                          # a retry or a crash: never twice
            return {"ok": row["verified"] is not None and bool(row["verified"]) or row["error"] == "", "verified": bool(row["verified"]),
                    "what": row["what"], "repeat": True, "note": "this was already done once; it was not run again"}
    clean, err = check_params(m, params)
    if clean is None:
        return {"ok": False, "verified": False, "error": err}
    if m.no_text and source == "text":
        return {"ok": False, "verified": False, "error": f"{m.name} never runs on something she read in mail, a page or the screen: only on his own words"}
    if m.risk == "never":
        _log(ctx, call_id or f"never-{ctx.clock.now()}", m.name, clean, m.risk, False, "", None, "", None, "refused: never", source, protocol)
        return {"ok": False, "verified": False, "error": f"{m.name} is something she never does"}
    if m.check is not None and (bad := m.check(ctx, clean)):
        return {"ok": False, "verified": False, "error": bad}
    asked, answer = False, ""
    if m.risk == "ask":
        if confirm is None:
            return {"ok": False, "verified": False, "error": "this needs his yes and there is no way to ask right now"}
        line = m.preview(clean, ctx) if getattr(m.preview, "wants_ctx", False) else m.preview(clean)
        if source == "text":
            line += " (this came from text on a page or in mail, not from him)"
        asked = True
        try:                                                         # one yes covers this one move only: every ask move calls confirm itself
            yes = bool(await asyncio.wait_for(confirm(line), CONFIRM_SECONDS))
            answer = "yes" if yes else "no"
        except asyncio.TimeoutError:
            yes, answer = False, "timeout"
        if not yes:
            why = "no answer in time" if answer == "timeout" else "he said no"
            _log(ctx, call_id or f"no-{ctx.clock.now()}", m.name, clean, m.risk, True, answer, None, why, None, "", source, protocol)
            return {"ok": False, "verified": False, "declined": True, "timed_out": answer == "timeout", "say": m.no_line,
                    "error": f"{why}: nothing was done. Say \"{m.no_line}\" and stop; never say it was done"}
    try:
        out = await m.run(ctx, clean)
    except Exception as e:                                           # a move never crashes the turn
        _log(ctx, call_id or f"err-{ctx.clock.now()}", m.name, clean, m.risk, asked, answer, False, "", None, str(e) or type(e).__name__, source, protocol)
        return {"ok": False, "verified": False, "error": f"{m.name} failed: {str(e) or type(e).__name__}"}
    if out.ok and m.verify is not None:
        try:
            out.verified = bool(await m.verify(ctx, clean, out))
        except Exception:
            out.verified = False
    elif out.ok:
        out.verified = False                                         # no read-back means never "proved"
    log_event("MOVE", code=f"{m.name}:{'verified' if out.verified else 'unverified' if out.ok else 'failed'}")
    _log(ctx, call_id or f"{m.name}-{ctx.clock.now()}", m.name, clean, m.risk, asked, answer, out.verified if out.ok else False,
         out.what, out.undo, "" if out.ok else out.what, source, protocol)
    r = _result(out)
    if out.ok and not out.verified:
        r["note"] = "it ran but could not be proved: do not say it worked"
    return r


# ---- D8: what she did, and taking the last thing back

def recent(ctx, limit: int = 30, since: float | None = None) -> list[dict]:
    """Newest first, straight from moves_log. A move that was refused or declined is listed too (verified false)."""
    q, args = "SELECT * FROM moves_log", []
    if since is not None:
        q += " WHERE at >= ?"
        args.append(since)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 200)))
    out = []
    for r in ctx.con.execute(q, args):
        out.append({"id": r["id"], "at": r["at"], "name": r["name"], "what": r["what"] or r["error"] or "", "risk": r["risk"],
                    "asked": bool(r["asked"]), "answer": r["answer"], "verified": None if r["verified"] is None else bool(r["verified"]),
                    "undoable": _undo_spec(r) is not None, "protocol": r["protocol"], "source": r["source"]})
    return out


def _undo_spec(row) -> dict | None:
    """The stored undo, only if it names a move and its params (a bare note is not enough to act on)."""
    try:
        u = json.loads(row["undo"]) if row["undo"] else None
    except (TypeError, ValueError):
        return None
    return u if isinstance(u, dict) and isinstance(u.get("move"), str) and isinstance(u.get("params"), dict) else None


async def undo(ctx, which="last", confirm: Confirm | None = None) -> dict:
    """Take back one move: `last` (the newest one that stored an undo and was not already undone) or a moves_log id. Only moves
    that stored an undo can be undone. Undoing a move that asked is itself shown first. The undo goes through `run`, so it is a
    second row in the log, with its own call id (the same one twice runs once)."""
    if str(which or "last").lower() == "last":
        rows = ctx.con.execute("SELECT * FROM moves_log WHERE undo != '' AND verified=1 ORDER BY id DESC").fetchall()
        row = next((r for r in rows if _undo_spec(r)), None)
    else:
        try:
            row = ctx.con.execute("SELECT * FROM moves_log WHERE id=?", (int(which),)).fetchone()
        except (TypeError, ValueError):
            return {"ok": False, "verified": False, "error": "which must be last or a number from the list of what she did"}
    if row is None:
        return {"ok": False, "verified": False, "error": "there is nothing she did that can be taken back"}
    if ctx.con.execute("SELECT 1 FROM moves_log WHERE call_id=?", (f"undo-{row['id']}",)).fetchone():
        return {"ok": False, "verified": False, "error": "that was already taken back"}
    spec = _undo_spec(row)
    if spec is None:
        return {"ok": False, "verified": False, "error": f"{row['name']} can't be taken back: it stored no way to undo it"}
    if row["risk"] == "ask":
        if confirm is None:
            return {"ok": False, "verified": False, "error": "this needs his yes and there is no way to ask right now"}
        try:
            yes = bool(await asyncio.wait_for(confirm(f"Take this back? {(row['what'] or row['name'])[:200]}"), CONFIRM_SECONDS))
        except asyncio.TimeoutError:
            yes = False
        if not yes:
            return {"ok": False, "verified": False, "declined": True, "say": "Okay, I left it.", "error": "he did not say yes: nothing was undone"}
    r = await run(ctx, spec["move"], spec["params"], f"undo-{row['id']}", confirm, "him")
    if r.get("ok"):
        with ctx.con:
            ctx.con.execute("UPDATE moves_log SET undo='' WHERE id=?", (row["id"],))
    return {**r, "undid": row["name"], "undid_id": row["id"]}
