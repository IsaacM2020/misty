"""Her own timers (step D, D2). `timer_start` writes a row; when it runs out she tells him through the gate as a `now`
Moment he set up himself (`own`): it may be said while he types, never in class or on a call or at night."""
from __future__ import annotations

from . import gate
from .logs import log_event


def start(ctx, minutes: int, label: str) -> int:
    now = ctx.clock.now()
    label = (label or "").strip()
    with ctx.con:
        ctx.con.execute("UPDATE timers SET status='stopped', closed_at=? WHERE status='active' AND label=?", (now, label))   # one per label
        cur = ctx.con.execute("INSERT INTO timers(label, started_at, ends_at) VALUES (?,?,?)", (label, now, now + minutes * 60))
    return int(cur.lastrowid)


def get(ctx, tid: int):
    return ctx.con.execute("SELECT * FROM timers WHERE id=?", (tid,)).fetchone()


def active(ctx) -> list:
    return ctx.con.execute("SELECT * FROM timers WHERE status='active' ORDER BY ends_at").fetchall()


def stop(ctx, label: str = "") -> list[int]:
    """Stops the timer with this label (or, with no label, every running one). Returns the ids stopped."""
    now = ctx.clock.now()
    label = (label or "").strip().lower()
    ids = [r["id"] for r in active(ctx) if not label or label in r["label"].lower()]
    with ctx.con:
        for i in ids:
            ctx.con.execute("UPDATE timers SET status='stopped', closed_at=? WHERE id=?", (now, i))
    return ids


def moment(row) -> gate.Moment:
    what = f"Your {row['label']} timer is up." if row["label"] else "Your timer is up."
    return gate.Moment(what, "now", topic="timer", topic_text="your timer", key=f"timer:{row['id']}", plain=True, own=True)


async def fire_due(ctx) -> int:
    now = ctx.clock.now()
    rows = ctx.con.execute("SELECT * FROM timers WHERE status='active' AND ends_at<=? ORDER BY ends_at", (now,)).fetchall()
    for r in rows:
        with ctx.con:                                   # closed first: a crash never tells him twice
            ctx.con.execute("UPDATE timers SET status='done', closed_at=? WHERE id=?", (now, r["id"]))
        await gate.submit(ctx, moment(r))
        log_event("TIMER_FIRED")
    return len(rows)
