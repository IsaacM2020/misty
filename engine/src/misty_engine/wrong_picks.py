"""The wrong-pick log (step B, card B2b): when she did the wrong thing and he corrects her, that exact phrase becomes a
candidate line for his tool-pick set (`scripts/checks/toolpick.py`), so the same miss cannot come back quietly.

The engine, not the model, fills in what he said and what she picked, from the turn before (chat_messages). The model only
says which tool she should have called (`wanted`) and a short note. Nothing joins his set without his yes
(`misty picks add <id>`). Real phrases live in the db and in ~/Misty/checks, never in the repo.
"""
from __future__ import annotations

import json
import secrets
import shutil
import time
from pathlib import Path

from .logs import log_event
from .scrub import find_secrets

NOTE_MAX = 300
SAID_MAX = 400
ARGS_MAX = 200


def _clip(s, n: int) -> str:
    return " ".join(str(s or "").split())[:n]


def previous_turn(con) -> tuple[str, str, str] | None:
    """(what he said, the tool she called or 'none', its args as JSON) for the turn BEFORE the one he is saying now."""
    users = con.execute("SELECT id, text FROM chat_messages WHERE role='user' ORDER BY id DESC LIMIT 2").fetchall()
    if len(users) < 2:
        return None
    prev = users[1]
    reply = con.execute("SELECT meta FROM chat_messages WHERE role='assistant' AND id>? AND id<? ORDER BY id LIMIT 1",
                        (prev["id"], users[0]["id"])).fetchone()
    if reply is None:
        return None
    try:
        calls = json.loads(reply["meta"] or "{}").get("calls") or []
    except json.JSONDecodeError:
        calls = []
    if not calls:
        return prev["text"], "none", ""
    return prev["text"], str(calls[0].get("name") or "none"), json.dumps(calls[0].get("args") or {}, ensure_ascii=False)


def record(ctx, wanted: str, note: str = "") -> dict:
    """Save a wrong pick, unless there is nothing to learn from. {saved: bool, ...}."""
    wanted = _clip(wanted, 40).lower()
    if not wanted:
        return {"ok": False, "saved": False, "error": "which tool should you have called, or none?"}
    turn = previous_turn(ctx.con)
    if turn is None:
        return {"ok": True, "saved": False, "why": "no earlier turn to compare with"}
    said, picked, args = turn
    if picked == wanted:
        return {"ok": True, "saved": False, "why": "that is the tool you called"}
    if find_secrets(f"{said} {args} {note}", "file"):
        log_event("WRONG_PICK_REFUSED_SECRET", n=1)
        return {"ok": True, "saved": False, "why": "it looks like it holds a password or key"}
    pid = "wp_" + secrets.token_hex(5)
    with ctx.con:
        ctx.con.execute("INSERT INTO wrong_picks(id, said, picked_tool, picked_args, wanted_tool, note, status, created_at) "
                        "VALUES (?,?,?,?,?,?, 'new', ?)",
                        (pid, _clip(said, SAID_MAX), picked, _clip(args, ARGS_MAX), wanted, _clip(note, NOTE_MAX), ctx.clock.now()))
    log_event("WRONG_PICK_SAVED", n=1)
    return {"ok": True, "saved": True, "picked": picked, "wanted": wanted}


def waiting(con) -> list:
    return con.execute("SELECT * FROM wrong_picks WHERE status='new' ORDER BY created_at").fetchall()


def get(con, pid: str):
    return con.execute("SELECT * FROM wrong_picks WHERE id=?", (pid,)).fetchone()


def yaml_line(row) -> str:
    """One line of the tool-pick file. A JSON string is a valid YAML string, so quotes and accents survive."""
    return "- {say: %s, expect_tool: %s}" % (json.dumps(row["said"], ensure_ascii=False), json.dumps(row["wanted_tool"]).strip('"'))


def add_to_set(con, pid: str, yaml_path: Path, backup_dir: Path, now: float | None = None) -> dict:
    """Append the pick to his tool-pick file, after copying the file to the backup folder. Only on his yes."""
    row = get(con, pid)
    if row is None or row["status"] != "new":
        return {"ok": False, "error": "no such waiting pick"}
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if yaml_path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"toolpick-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now or time.time()))}.yaml"
        shutil.copy2(yaml_path, backup)
    text = yaml_path.read_text() if yaml_path.exists() else ""
    with open(yaml_path, "a") as f:
        f.write(("" if text.endswith("\n") or not text else "\n") + yaml_line(row) + "\n")
    with con:
        con.execute("UPDATE wrong_picks SET status='added' WHERE id=?", (pid,))
    log_event("WRONG_PICK_ADDED", n=1)
    return {"ok": True, "backup": str(backup) if backup else None}


def dismiss(con, pid: str) -> dict:
    row = get(con, pid)
    if row is None or row["status"] != "new":
        return {"ok": False, "error": "no such waiting pick"}
    with con:
        con.execute("UPDATE wrong_picks SET status='dismissed' WHERE id=?", (pid,))
    return {"ok": True}
