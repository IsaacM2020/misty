"""JSON-RPC method table. M1: status and control only."""
from __future__ import annotations

from typing import Any

from .ctx import Ctx
from .errors import Fatal


def _build_status(ctx: Ctx):
    from .build import Build
    return Build(ctx).status(include_done=False)


def _badge(ctx: Ctx) -> bool:
    from . import suggest
    return suggest.badge(ctx.con)


# Jobs that "failed" only because paid calls were off, Google needs reconnecting, a newer job replaced them, or Isaac
# deleted his data aren't errors: the strip showed "407 failed" for background notes skipped days ago while paid calls
# were locked, and "323 failed" for Google checks made while Google was signed out.
SKIPPED = ("PAID_LOCKED", "NEEDS_AUTH", "SUPERSEDED", "DELETED")


def _queue(ctx: Ctx) -> dict[str, int]:
    q = ctx.queue.counts()
    n = ctx.con.execute(f"SELECT COUNT(*) FROM jobs WHERE status='failed' AND error_code IN ({','.join('?' * len(SKIPPED))})",
                        SKIPPED).fetchone()[0]
    if n:
        q["failed"] = q.get("failed", 0) - n
        q["skipped"] = n
    return q


def capped_causes(ctx: Ctx) -> list[dict[str, Any]]:
    """Which cap is holding back the waiting_cap jobs: one row per cap with how many jobs wait, what is spent and the cap.
    group is "daily", "build" or the purpose group (judge, summarise...)."""
    rows = ctx.con.execute(
        "SELECT COALESCE(error_code,'CAP_DAILY') AS code, COUNT(*) AS n FROM jobs WHERE status='waiting_cap' GROUP BY 1").fetchall()
    out = []
    for r in rows:
        code, n = r["code"], r["n"]
        if code.startswith("CAP_PURPOSE:"):
            group = code.split(":", 1)[1]
            sub = ctx.spend.purpose_cap(group, "live")
            spent, cap = ctx.spend.spent_purpose(group, "live"), (sub[1] if sub else 0.0)
        elif code == "CAP_BUILD":
            group, spent, cap = "build", 0.0, float(ctx.settings.get("cap_build_usd"))
        else:
            group, spent, cap = "daily", ctx.spend.spent_today(), float(ctx.settings.get("cap_daily_usd"))
        out.append({"group": group, "waiting": n, "spent": spent, "cap": cap})
    return out


def status_snapshot(ctx: Ctx) -> dict[str, Any]:
    s = ctx.settings
    build_row = ctx.con.execute(
        "SELECT id FROM builds WHERE status IN ('running','paused') ORDER BY started_at DESC LIMIT 1").fetchone()
    sources = [
        {"source": r["source"], "health": r["health"], "last_ok_at": r["last_ok_at"]}
        for r in ctx.con.execute("SELECT source, health, last_ok_at FROM sources_state ORDER BY source")
    ]
    return {
        "online": ctx.net.online,
        "paused": bool(s.get("paused")),
        "queue": _queue(ctx),
        "capped": capped_causes(ctx),
        "spend": {
            "today": ctx.spend.spent_today(),
            "cap_daily": float(s.get("cap_daily_usd")),
            "build": ctx.spend.spent_build(build_row["id"]) if build_row else None,
            "cap_build": float(s.get("cap_build_usd")),
        },
        "build": _build_status(ctx),
        "sources": sources,
        "badge": _badge(ctx),
        "health": s.get("health_summary"),
    }


async def _push_status(ctx: Ctx) -> None:
    if ctx.notify:
        await ctx.notify("status.changed", status_snapshot(ctx))


def build_methods(ctx: Ctx) -> dict:
    async def status_get(_: dict) -> dict:
        return status_snapshot(ctx)

    async def pause(_: dict) -> dict:
        ctx.settings.set("paused", True)
        await _push_status(ctx)
        return {"paused": True}

    async def resume(_: dict) -> dict:
        ctx.settings.set("paused", False)
        await _push_status(ctx)
        return {"paused": False}

    async def net_set(p: dict) -> dict:
        online = bool(p.get("online"))
        changed = ctx.net.set_online(online, "app")
        if online:
            ctx.queue.release("waiting_network")
        if changed:
            await _push_status(ctx)
        return {"online": ctx.net.online}

    from . import cost_report
    from .api_chat import chat_methods
    from .api_class import class_methods
    from .api_class_live import class_live_methods
    from .api_setup import setup_methods
    from .api_wiki import wiki_methods
    from .stream.api import stream_methods
    async def spend_breakdown(p: dict) -> dict:
        days, n = p.get("days", 7), p.get("messages", 20)
        if not isinstance(days, int) or not isinstance(n, int) or isinstance(days, bool) or isinstance(n, bool):
            raise Fatal("BAD_PARAMS")
        return cost_report.breakdown(ctx.con, ctx.clock.now(), days, max(0, min(n, 100)))

    async def cares_list(_p: dict) -> dict:
        """What Isaac has told Misty matters to him (the Radar tab). View only: changes happen in chat."""
        from . import cares
        return {"cares": [{**cares.as_dict(r), "level": cares.LEVEL_WORDS.get(r["weight"], "matters")}
                          for r in cares.active(ctx.con)]}

    async def follows_list(_p: dict) -> dict:
        """What Misty is keeping an eye on, with the newest thing each one found (the Radar tab). View only."""
        from . import follows
        out = []
        for f in follows.active(ctx.con):
            fd = follows.findings(ctx.con, f["id"], 1)
            out.append({"id": f["id"], "text": f["text"], "source": f["source"], "every_min": f["every_min"],
                        "until": follows._now_str(f["until_at"]), "checks": f["checks"], "last_error": f["last_error"],
                        "last_checked": follows._now_str(f["last_at"]) if f["last_at"] else None,
                        "latest": fd[0]["text"] if fd else None, "latest_source": fd[0]["source_name"] if fd else None})
        return {"follows": out}

    async def presence_set(p: dict) -> dict:
        """The app says how busy he is (seconds since his last key or mouse move, and whether he is talking with her).
        The gate reads it before she speaks up on her own."""
        from . import gate
        idle = p.get("idle_s")
        gate.set_presence(ctx, idle if isinstance(idle, (int, float)) else None, str(p.get("voice") or "idle"))
        return {"ok": True}

    async def told_list(p: dict) -> dict:
        """What she told him on her own, newest first, and what is waiting (the Radar tab)."""
        from . import feedback, gate
        n = max(1, min(int(p.get("limit", 30)), 100))
        held = [dict(r) for r in ctx.con.execute("SELECT id, at, urgency, topic_text, text, source_name, why FROM held "
                                                 "WHERE status='waiting' ORDER BY at")]
        return {"told": feedback.listing(ctx.con, n), "held": held, **gate.describe(ctx)}

    async def lessons_list(_p: dict) -> dict:
        """Her lessons about when to tell him things, and the last check against the saved mistakes (the Radar tab)."""
        from . import lessons
        r = ctx.con.execute("SELECT at, verdict, total, passed FROM replays ORDER BY at DESC LIMIT 1").fetchone()
        return {"lessons": lessons.as_list(ctx.con), "last_replay": dict(r) if r else None}

    async def ways_list(_p: dict) -> dict:
        """How she is with him: the active ways (the Radar tab). View only: he changes them by telling her. Built from the
        same rows as the ways list in her prompt, so what the tab shows is what she says when asked how she is set up."""
        from . import ways
        return {"ways": [ways.as_dict(r) for r in ways.active(ctx.con)]}

    async def moves_list(p: dict) -> dict:
        """What she did for him, newest first (the Radar tab). View only: he undoes things by telling her."""
        from . import moves
        return {"moves": moves.recent(ctx, int(p.get("limit", 30)))}

    async def protocols_list(_p: dict) -> dict:
        """His protocols, in plain words (the Radar tab). View only."""
        from . import protocols
        return {"protocols": [protocols.describe(r) for r in protocols.active(ctx.con)]}

    async def teach_overview(_p: dict) -> dict:
        """Where he is in what he is learning (the Radar tab). View only, counted from rows, no model call."""
        from .teach import overview
        return overview.overview(ctx)

    async def phrases_add(p: dict) -> dict:
        """The app heard a final and says how it went (F6). Kept only when he turned collecting on; scrubbed text only."""
        from . import phrases
        since = p.get("since_reply")
        return phrases.add(ctx, str(p.get("text") or ""), state=str(p.get("state") or ""), named=bool(p.get("named")),
                           followup=bool(p.get("followup")), since_reply=since if isinstance(since, (int, float)) else None,
                           outcome=str(p.get("outcome") or ""), private=bool(p.get("private")), class_mode=bool(p.get("class_mode")))

    async def phrases_state(_p: dict) -> dict:
        from . import phrases
        return phrases.state(ctx)

    async def phrases_clear(_p: dict) -> dict:
        from . import phrases
        return {"cleared": phrases.clear(ctx)}

    async def activity_note(p: dict) -> dict:
        """The app saw him switch app or window. Counts only are logged."""
        from . import activity
        return activity.note(ctx, str(p.get("bundle") or ""), str(p.get("app") or ""), str(p.get("title") or ""), str(p.get("doc") or ""))

    async def privacy_list(_p: dict) -> dict:
        """The apps she can't see (the Radar tab, view only: he changes them by telling her)."""
        from . import privacy
        return {"apps": privacy.apps(ctx)}

    async def told_rate(p: dict) -> dict:
        """A thumb on one line she told him (the buttons on the Radar tab)."""
        from . import feedback
        return feedback.rate(ctx, str(p.get("id") or ""), str(p.get("value") or ""))

    async def told_interrupted(p: dict) -> dict:
        """He talked over a line she began on her own (F4): the line is marked interrupted, not rated."""
        from . import feedback
        return feedback.interrupted(ctx, str(p.get("id") or ""))

    return {"presence.set": presence_set, "told.list": told_list, "told.rate": told_rate, "told.interrupted": told_interrupted, "lessons.list": lessons_list, "ways.list": ways_list, "moves.list": moves_list, "protocols.list": protocols_list, "teach.overview": teach_overview, "phrases.add": phrases_add, "phrases.state": phrases_state, "phrases.clear": phrases_clear, "privacy.list": privacy_list, "activity.note": activity_note, "status.get": status_get, "spend.breakdown": spend_breakdown, "cares.list": cares_list, "follows.list": follows_list, "control.pause": pause, "control.resume": resume, "net.set": net_set,
            **setup_methods(ctx), **wiki_methods(ctx), **stream_methods(ctx), **chat_methods(ctx),
            **class_methods(ctx), **class_live_methods(ctx)}
