"""Stream JSON-RPC methods: the app registers as the hands, answers hands.request, and reports the person's own input."""
from __future__ import annotations

import asyncio

from ..ctx import Ctx
from ..errors import Fatal
from .bridge import AppHands

# Read-only hands ops the dev probe (hands.call) may run. read_window_text is for G0's catch_where probe.
PROBE_OPS = ("permissions", "front_app", "ax_snapshot", "menu_list", "window_list", "screen_frame",
             "capture_window", "cursor", "read_window_text")


def stream_methods(ctx: Ctx) -> dict:
    async def to_app(method: str, params: dict) -> None:
        if ctx.notify is not None:
            await ctx.notify(method, params)
    hands = AppHands(to_app)
    ctx.extras = {**(ctx.extras or {}), "hands": hands}

    async def hands_register(p: dict) -> dict:
        hands.register()
        from .. import privacy
        asyncio.get_running_loop().create_task(privacy.push(ctx))        # the app learns which apps are private
        return {"ok": True}

    async def hands_result(p: dict) -> dict:
        req_id = p.get("req_id")
        if not isinstance(req_id, str):
            raise Fatal("BAD_PARAMS")
        hands.result(req_id, bool(p.get("ok")), p.get("data"), p.get("code") if isinstance(p.get("code"), str) else None)
        return {"ok": True}

    async def hands_user_input(p: dict) -> dict:
        ts = p.get("ts")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            raise Fatal("BAD_PARAMS")
        hands.user_input_seen(ts)
        return {"ok": True}

    async def hands_call(p: dict) -> dict:
        """Dev probe: run one read-only hands op from the CLI (e.g. front_app, ax_snapshot, menu_list)."""
        op, args = p.get("op"), p.get("args") or {}
        if op not in PROBE_OPS:
            raise Fatal("BAD_PARAMS")
        return {"data": await hands._call(op, **args)}

    async def stream_run(p: dict) -> dict:
        """Run one task. confirm: 'deny' (bench default) or 'yes'; the app's confirm card comes with the panel."""
        from dataclasses import asdict
        from . import loop
        from .brain import Brain
        task = p.get("task")
        if not isinstance(task, str) or not task.strip() or len(task) > 2000:
            raise Fatal("BAD_PARAMS")
        brain = Brain(ctx, str(p.get("brain") or "haiku"))
        start = await hands.open_app(str(p["app"])) if p.get("app") else None
        mode = p.get("confirm", "deny")
        asked: list[str] = []

        async def confirm(q: str) -> bool:
            asked.append(q)
            return mode == "yes"

        async def status(text: str) -> None:
            if ctx.notify is not None:
                await ctx.notify("stream.status", {"text": text})

        r = await loop.run(task, hands=hands, brain=brain, home=ctx.paths.home, context=str(p.get("context") or ""),
                           confirm=confirm, on_status=status, start_app=start,
                           max_usd=min(float(p.get("max_usd") or loop.MAX_USD), 0.25))
        out = asdict(r)
        out["asked"] = asked
        return out

    return {"stream.run": stream_run, "hands.register": hands_register, "hands.result": hands_result, "hands.user_input": hands_user_input,
            "hands.call": hands_call}
