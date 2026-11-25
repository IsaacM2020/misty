"""`class.correct`: one round of live transcript v2 (see class_live.py). Kept apart from api_class.py so the class
store and its other methods stay untouched."""
from __future__ import annotations

from . import class_live
from .ctx import Ctx
from .errors import Fatal


def class_live_methods(ctx: Ctx) -> dict:
    async def correct(p: dict) -> dict:
        sid = p.get("session")
        if not isinstance(sid, str) or not sid.strip() or len(sid) > 40:
            raise Fatal("BAD_PARAMS")
        return await class_live.correct_round(ctx, sid)

    return {"class.correct": correct}
