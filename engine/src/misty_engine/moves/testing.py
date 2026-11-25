"""D3: `test_write`, a move for testing the yes flow without touching anything real. NOT imported by the package: it is
never in the model's catalog. Tests and `scripts/checks/moves_ask.py` call `register_test_write()`."""
from __future__ import annotations

from .base import Move, Outcome, Param
from .registry import register


async def _run(ctx, p) -> Outcome:
    with ctx.con:
        ctx.con.execute("CREATE TABLE IF NOT EXISTS test_write_rows(id INTEGER PRIMARY KEY AUTOINCREMENT, what TEXT NOT NULL)")
        ctx.con.execute("INSERT INTO test_write_rows(what) VALUES (?)", (p["what"],))
    return Outcome(True, what=f"wrote {p['what']}")


async def _verify(ctx, p, out) -> bool:
    return ctx.con.execute("SELECT 1 FROM test_write_rows WHERE what=?", (p["what"],)).fetchone() is not None


def rows(ctx) -> list[str]:
    try:
        return [r[0] for r in ctx.con.execute("SELECT what FROM test_write_rows ORDER BY id")]
    except Exception:
        return []


def register_test_write() -> Move:
    return register(Move("test_write", "test only", {"what": Param(str, max_len=40)}, "ask", _run, _verify,
                         preview=lambda p: f"Write \"{p['what']}\" to the test table?", no_line="Okay, not written.",
                         says=("a", "b", "c"), not_mine=(("a", "do"), ("b", "do"))))
