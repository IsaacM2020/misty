"""Chat + facts JSON-RPC methods. chat.send streams `chat.delta` and `chat.status` events while it works, and a risky
step inside a task shows up as `chat.confirm`, which only Isaac's chat.answer can let through. chat.stop ends a turn's
reply where it is, whether it is still waiting its turn or already answering."""
from __future__ import annotations

import time

import asyncio
import json
import secrets

from . import chat, facts, hearing
from .frame import parse as frame_parse
from .ctx import Ctx
from .turn_timing import Marks
from .errors import Fatal

CONFIRM_WAIT = 180.0       # seconds Isaac has to answer a confirm before it counts as no


def chat_methods(ctx: Ctx) -> dict:
    waiting: dict[str, asyncio.Future] = {}
    stops: dict[str, asyncio.Event] = {}
    asker: dict[str, str] = {}                            # confirm id -> its turn
    busy = asyncio.Lock()

    async def emit(method: str, params: dict) -> None:
        if ctx.notify is not None:
            await ctx.notify(method, params)

    async def send(p: dict) -> dict:
        text, turn, voice = p.get("text"), p.get("turn"), p.get("voice", False)
        if (not isinstance(text, str) or not isinstance(turn, str) or not turn or len(turn) > 64
                or not isinstance(voice, bool)):
            raise Fatal("BAD_PARAMS")

        async def on_delta(piece: str) -> None:
            await emit("chat.delta", {"turn": turn, "text": piece})

        async def on_status(s: str) -> None:
            await emit("chat.status", {"turn": turn, "text": s[:120]})

        async def confirm(question: str) -> bool:
            cid = "c_" + secrets.token_hex(4)
            fut = asyncio.get_running_loop().create_future()
            waiting[cid], asker[cid] = fut, turn
            await emit("chat.confirm", {"turn": turn, "id": cid, "question": question[:300]})
            try:
                return bool(await asyncio.wait_for(fut, CONFIRM_WAIT))
            except asyncio.TimeoutError:
                return False
            finally:
                waiting.pop(cid, None)
                asker.pop(cid, None)

        ctx.turn = turn
        fr = frame_parse(p.get("frame"))
        stop = stops[turn] = asyncio.Event()             # outside the lock: stop works while it runs or waits
        marks = Marks() if voice else None               # timing only: a voice turn's numbers go to voice.log
        try:
            async with busy:                              # one message at a time, in order
                if marks:
                    marks.mark("lock_got")
                out = await chat.send(ctx, text, on_delta=on_delta, on_status=on_status, confirm=confirm,
                                      voice=voice, stop=stop, frame=fr, **({"marks": marks} if marks else {}))
        finally:
            stops.pop(turn, None)
            if marks:
                marks.mark("end")
                marks.write(ctx.paths.logs / "voice.log", turn)
        await emit("chat.done", {"turn": turn, **({"stopped": True} if out.get("stopped") else {})})
        return out

    async def stop(p: dict) -> dict:
        turn = str(p.get("turn"))
        ev = stops.get(turn)
        if ev is None:
            return {"ok": False}
        ev.set()
        for cid, t in list(asker.items()):                # a confirm it was waiting on counts as no
            fut = waiting.get(cid)
            if t == turn and fut is not None and not fut.done():
                fut.set_result(False)
        return {"ok": True}

    async def answer(p: dict) -> dict:
        fut = waiting.get(str(p.get("id")))
        if fut is None or fut.done():
            return {"ok": False}
        fut.set_result(p.get("yes") is True)
        return {"ok": True}

    async def hist(p: dict) -> dict:
        n = p.get("limit", 60)
        n = n if isinstance(n, int) and 0 < n <= 500 else 60
        return {"messages": [{"id": r["id"], "role": r["role"], "text": r["text"],
                              "meta": json.loads(r["meta"]) if r["meta"] else None, "created_at": r["created_at"]}
                             for r in chat.history(ctx.con, n)]}

    async def facts_list(p: dict) -> dict:
        return {"facts": [facts.as_dict(r) for r in facts.active(ctx.con)]}

    async def facts_forget(p: dict) -> dict:
        fid = p.get("id")
        if not isinstance(fid, str):
            raise Fatal("BAD_PARAMS")
        return {"forgotten": facts.forget(ctx, fid)}

    async def hear(p: dict) -> dict:
        text, last, since = p.get("text"), p.get("last_reply", ""), p.get("since_reply")
        if not isinstance(text, str) or not isinstance(last, str) or not (since is None or isinstance(since, (int, float))):
            raise Fatal("BAD_PARAMS")
        return await hearing.hear(ctx, text, last_reply=last, since_reply=since)

    async def addressed_judge(p: dict) -> dict:
        """F7: was that phrase for her? Only `answers: true` means she may answer. Without the pass file this asks no model."""
        from . import addressed
        text, last, since, named, fr = p.get("text"), p.get("last_reply", ""), p.get("since_reply"), p.get("named", False), p.get("frame")
        room = p.get("room")
        if (not isinstance(text, str) or not isinstance(last, str) or not (since is None or isinstance(since, (int, float)))
                or not isinstance(named, bool) or not (fr is None or isinstance(fr, dict))
                or not (room is None or (isinstance(room, int) and not isinstance(room, bool)))):
            raise Fatal("BAD_PARAMS")
        r = await addressed.judge(ctx, text, last_reply=last, since_reply=since, frame=fr, named=named, room=room)
        if r.get("label") == "write":          # ambient dictation: his words go straight into the box, no chat turn, nothing sent
            from . import moves
            w = await moves.run(ctx, "write_here", {"text": r.get("why", "")}, f"aw{int(time.time() * 1000)}")
            return {"label": "write", "why": "", "text": r.get("why", ""), "usd": r.get("usd", 0.0), "answers": False, "wrote": bool(w.get("ok")),
                    "app": str(w.get("app") or "")}
        return {**r, "answers": addressed.answers(r)}

    async def addressed_state(_p: dict) -> dict:
        from . import addressed
        return addressed.state(ctx)

    async def warm(p: dict) -> dict:
        voice = p.get("voice", True)
        if not isinstance(voice, bool):
            raise Fatal("BAD_PARAMS")
        return await chat.warm(ctx, voice=voice)         # no busy lock: a warm never queues behind (or in front of) a real turn

    async def slate_list(p: dict) -> dict:
        from . import slate
        n = p.get("limit", 100)
        return {"slates": slate.listing(ctx, n if isinstance(n, int) and 0 < n <= 500 else 100)}

    async def slate_get(p: dict) -> dict:
        from . import slate
        sid = p.get("id")
        if not isinstance(sid, int):
            raise Fatal("BAD_PARAMS")
        page = slate.get(ctx, sid)
        if page is None:
            raise Fatal("BAD_PARAMS")
        return {"slate": page}

    return {"slate.list": slate_list, "slate.get": slate_get, "live.hear": hear, "addressed.judge": addressed_judge, "addressed.state": addressed_state, "chat.send": send, "chat.warm": warm, "chat.stop": stop, "chat.answer": answer, "chat.history": hist,
            "facts.list": facts_list, "facts.forget": facts_forget}
