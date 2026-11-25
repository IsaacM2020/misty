"""Class Notes JSON-RPC methods. The app sends each phrase its ears finish as `class.segment`; the engine keeps the
text (never audio), Isaac's typed notes and the gaps, and makes the notes when the class ends."""
from __future__ import annotations

import asyncio

from . import class_ask, class_notes, classes
from .ctx import Ctx
from .errors import Fatal

SOURCES = ("room", "mac")


def _str(p: dict, key: str, *, required: bool = True, n: int = 200) -> str | None:
    v = p.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip() or len(v) > n:
        raise Fatal("BAD_PARAMS")
    return v


def _num(p: dict, key: str) -> float:
    v = p.get(key)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
        raise Fatal("BAD_PARAMS")
    return float(v)


def class_methods(ctx: Ctx) -> dict:
    con, clock = ctx.con, ctx.clock
    running: dict[str, asyncio.Task] = {}                 # session -> its notes job, so one runs at a time

    def notes_later(sid: str) -> None:
        if sid in running and not running[sid].done():
            return
        running[sid] = asyncio.create_task(class_notes.make_notes(ctx, sid))

    async def start(p: dict) -> dict:
        title = _str(p, "title", required=False)
        if cal := _str(p, "cal_title", required=False, n=200):   # the calendar event he is in names the class
            title = classes.dated(cal, clock.now())
        return classes.start(con, clock, _str(p, "subject", n=40), title,
                             _str(p, "cal_event_id", required=False, n=1024), p.get("mode") or "room")

    async def rename(p: dict) -> dict:
        return classes.rename(con, _str(p, "session", n=40), _str(p, "title", required=False, n=200),
                              _str(p, "subject", required=False, n=40))

    async def segment(p: dict) -> dict:
        source = p.get("source", "room")
        text = p.get("text")
        if source not in SOURCES or not isinstance(text, str) or len(text) > 4000:
            raise Fatal("BAD_PARAMS")
        t0, t1 = _num(p, "t_start"), _num(p, "t_end")
        return {"id": classes.add_segment(con, _str(p, "session", n=40), t0, max(t0, t1), source, text)}

    async def slide(p: dict) -> dict:
        text = p.get("text")
        if not isinstance(text, str) or len(text) > 4000:
            raise Fatal("BAD_PARAMS")
        return {"id": classes.add_slide(con, _str(p, "session", n=40), _num(p, "t"), text)}

    async def now(p: dict) -> dict:
        """What the calendar says he is in right now, to pre-fill Start class: a meeting link means online."""
        from datetime import datetime
        from . import calendar_now
        from . import now as nowlib
        from .clock import SGT
        if not ctx.net.online:
            return {"found": False}
        t = datetime.fromtimestamp(clock.now(), SGT)
        evs = nowlib.cached(ctx, 60)                       # the shared 2-minute read; older than a minute and this polls Google itself
        if evs is None:
            if not await nowlib.refresh(ctx):              # not connected, offline, Google down: just no suggestion
                return {"found": False}
            evs = nowlib.cached(ctx, 60)
            if evs is None:
                return {"found": False}
        cur = calendar_now.current(evs, t)
        if cur is None:
            return {"found": False}
        from . import class_reach
        class_reach.with_saved_link(ctx, cur)              # the invite has no link: maybe he saved one for this class
        return {"found": True, **cur, **class_reach.suggest(ctx, cur)}

    async def reach(p: dict) -> dict:
        """The app is acting on a `suggest` (auto or ask): remember the event, say the line through the gate."""
        from . import class_reach
        how = _str(p, "suggest", n=10)
        return {"gate": await class_reach.reach(ctx, _str(p, "key", n=300), _str(p, "title", n=200), how)}

    async def notes_save(p: dict) -> dict:
        text = p.get("text")
        if not isinstance(text, str) or len(text) > 100_000:
            raise Fatal("BAD_PARAMS")
        classes.save_notes(con, _str(p, "session", n=40), text)
        return {"ok": True}

    async def pause(p: dict) -> dict:
        classes.pause(con, clock, _str(p, "session", n=40), _str(p, "reason", n=40))
        return {"ok": True}

    async def resume(p: dict) -> dict:
        classes.resume(con, clock, _str(p, "session", n=40))
        return {"ok": True}

    async def end(p: dict) -> dict:
        sid = _str(p, "session", n=40)
        classes.end(con, clock, sid)
        notes_later(sid)
        return classes.get(con, sid)["session"]

    async def regenerate(p: dict) -> dict:
        sid = _str(p, "session", n=40)
        if classes.get(con, sid)["session"]["status"] in classes.OPEN:
            raise Fatal("CLASS_STILL_LIVE")
        notes_later(sid)
        return {"ok": True}

    def _ids(p: dict, lo: int) -> list[str]:
        ids = p.get("sessions")
        if not (isinstance(ids, list) and lo <= len(ids) <= 200 and all(isinstance(x, str) and 0 < len(x) <= 40 for x in ids)):
            raise Fatal("BAD_PARAMS")
        return ids

    def _forget(sess: dict) -> None:
        class_notes.forget_in_wiki(ctx, sess)                 # its lines come off the wiki and Priorities
        if ctx.wiki is not None:
            from .wiki import priorities as pr
            pr.remove_class(ctx.wiki, clock, sess["id"])

    async def merge(p: dict) -> dict:
        """Two or more parts of one lesson become one class with one set of notes. Optional `title` renames it."""
        out = classes.merge_many(con, _ids(p, 2), _str(p, "title", required=False, n=200))
        for gid in out["absorbed_all"]:
            _forget(classes.get(con, gid)["session"])         # the new notes put back what the whole lesson holds
        notes_later(out["kept"])
        return out

    async def ask(p: dict) -> dict:
        q = _str(p, "question", n=500)
        return await class_ask.ask(ctx, q, sid=_str(p, "session", required=False, n=40),
                                   subject=_str(p, "subject", required=False, n=40),
                                   since=_str(p, "since", required=False, n=10))

    def _tick(action_id: str | None, pid: str | None, done: bool) -> bool:
        """Ticking a class item in Priorities or in the class notes ticks it in both."""
        from .wiki import priorities as pr
        if action_id:
            r = con.execute("SELECT priority_id FROM class_actions WHERE id=?", (action_id,)).fetchone()
            if r is None:
                raise Fatal("NO_ACTION")
            pid = r["priority_id"]
            con.execute("UPDATE class_actions SET done=? WHERE id=?", (int(done), action_id))
        elif pid:
            con.execute("UPDATE class_actions SET done=? WHERE priority_id=?", (int(done), pid))
        return bool(pid and ctx.wiki is not None and pr.set_done(ctx.wiki, clock, pid, done))

    async def action_done(p: dict) -> dict:
        if not isinstance(p.get("done"), bool):
            raise Fatal("BAD_PARAMS")
        return {"ok": True, "priority": _tick(_str(p, "action", n=40), None, p["done"])}

    async def priority_done(p: dict) -> dict:
        if not isinstance(p.get("done"), bool):
            raise Fatal("BAD_PARAMS")
        return {"ok": _tick(None, _str(p, "id", n=40), p["done"])}

    async def get(p: dict) -> dict:
        return classes.get(con, _str(p, "session", n=40))

    async def list_(p: dict) -> dict:
        return {"classes": classes.list(con, _str(p, "subject", required=False, n=40))}

    async def delete(p: dict) -> dict:
        """One class (`session`) or several (`sessions`). The audio goes to the Trash."""
        ids = _ids(p, 1) if "sessions" in p else [_str(p, "session", n=40)]
        sess = [classes.get(con, i)["session"] for i in ids]
        from .class_audio import misty_home
        gone = classes.delete_many(con, ids, home=misty_home())
        for s_ in sess:
            _forget(s_)
        for g in gone:
            if g not in ids and ctx.wiki is not None:
                from .wiki import priorities as pr
                pr.remove_class(ctx.wiki, clock, g)
        return {"ok": True, "deleted": gone}

    return {"class.start": start, "class.now": now, "class.reach": reach, "class.segment": segment, "class.slide": slide, "class.notes.save": notes_save, "class.pause": pause,
            "class.resume": resume, "class.end": end, "class.regenerate": regenerate, "class.get": get, "class.action.done": action_done, "priorities.done": priority_done, "class.ask": ask,
            "class.list": list_, "class.merge": merge, "class.delete": delete, "class.rename": rename}
