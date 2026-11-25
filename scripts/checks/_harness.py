"""Shared setup for the paid wall checks (toolpick.py, anybody.py). Not a check itself.

Every check runs on a throwaway data folder, never on the live ~/Misty:
  copy_live=True   a copy of the live db (so the prompt is the real one), chat history emptied, writes go to the copy
  copy_live=False  an empty db, nothing about anyone (the anybody test)
The wiki folder is only linked read-only for the copy. A copy has its own spend meter, so a check never eats the
live daily cap; it still spends real dollars, which every check prints.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "src"))
sys.path.insert(0, str(ROOT / "tests" / "engine"))


def temp_ctx(copy_live: bool = True, home: Path | None = None):
    """Returns (ctx, home). The env var MISTY_HOME points at the throwaway folder before the engine reads paths."""
    home = home or Path(tempfile.mkdtemp(prefix="misty-check-"))
    os.environ["MISTY_HOME"] = str(home)
    from misty_engine.config import load_pinned
    from misty_engine.main import Engine
    from misty_engine.paths import MistyPaths
    paths = MistyPaths.from_env()
    paths.ensure()
    if copy_live:
        live = Path.home() / "Misty" / "misty.db"
        src, dst = sqlite3.connect(f"file:{live}?mode=ro", uri=True), sqlite3.connect(paths.db)
        src.backup(dst)                                    # a consistent copy, the live file is only read
        src.close()
        dst.execute("DELETE FROM chat_messages")           # each phrasing is judged alone
        try:
            dst.execute("DELETE FROM chat_summary")
        except sqlite3.OperationalError:
            pass
        dst.commit()
        dst.close()
        wiki = Path.home() / "Misty" / "wiki"
        if wiki.exists() and not paths.wiki.exists():
            os.symlink(wiki, paths.wiki)
    ctx = Engine(paths, load_pinned()).build_ctx()
    ctx.settings.set("cap_daily_usd", 5.0)                 # a check is a paid run of its own: Misty's $0.20/day cap and the copy's meter don't apply
    ctx.settings.set("purpose_caps_daily", {})
    return ctx, home


def cleanup(home: Path) -> None:
    shutil.rmtree(home, ignore_errors=True)
