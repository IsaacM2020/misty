"""Setup / build / cap methods of the JSON-RPC API (first-run flow and `misty` CLI share them)."""
from __future__ import annotations

import asyncio
from typing import Any

from . import keychain
from .build import Build, estimate
from .ctx import Ctx
from .errors import Fatal
from .logs import log_event
from .openrouter import user_approved
from .sources import google_auth

SOURCE_KEYS = ("folders", "repos", "claude_mem", "graphify", "gmail", "gcal", "imessage", "whatsapp")


def setup_methods(ctx: Ctx) -> dict:
    state: dict[str, Any] = {"task": None, "progress": None}

    async def _progress() -> None:
        while True:
            st = Build(ctx).status()
            if st and ctx.notify:
                await ctx.notify("build.progress", st)
            await asyncio.sleep(2)

    async def test_openrouter(_: dict) -> dict:
        # only ever sent by the app's Test button, so it is Isaac's own yes to two tiny calls (~$0.0001)
        with user_approved():
            r = await ctx.openrouter.chat(model_key="haiku",
                                          messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                                          max_tokens=5, job_id=None, phase="live", purpose="setup")
            jev_ok = True
            try:
                await ctx.jev.worth("A short note about a school project deadline.", job_id=None, phase="live")
            except Fatal as e:
                jev_ok = False
                log_event("SETUP_JEV_FAIL", code=e.code)
        return {"ok": True, "model": ctx.pinned.models.haiku, "zdr": True, "usd": round(r.cost_usd, 8),
                "jev_ok": jev_ok}

    async def check_fda(_: dict) -> dict:
        from .sources.imessage import IMessageSource
        return {"granted": await asyncio.to_thread(IMessageSource().check_access)}

    async def google_auth_start(_: dict) -> dict:
        email = await asyncio.to_thread(google_auth.begin_auth)
        enabled = dict(ctx.settings.get("sources_enabled"))
        enabled.update({"gmail": True, "gcal": True})
        ctx.settings.set("sources_enabled", enabled)
        ctx.con.execute("UPDATE sources_state SET health='ok', error_code=NULL "
                        "WHERE source IN ('gmail','gcal') AND health='needs_auth'")     # signed in again: polling resumes
        return {"email": email}

    async def import_client(p: dict) -> dict:
        google_auth.import_client_json(str(p.get("json", "")))
        return {"ok": True}

    async def set_key(p: dict) -> dict:
        key = str(p.get("key", "")).strip()
        if not key:
            raise Fatal("EMPTY_KEY")
        keychain.set_secret("openrouter", key)
        return {"ok": True}

    async def saved_state(_: dict) -> dict:
        """What earlier setup (CLI or app) already stored, so first-run can skip it. Local Keychain reads only."""
        return {"openrouter_key": bool(keychain.get_secret("openrouter")),
                "google_email": google_auth.ALLOWED_ACCOUNT if keychain.get_secret("google_token") else None}

    def _chats(src_cls_path: str):
        async def run(_: dict) -> dict:
            mod, cls = src_cls_path.rsplit(".", 1)
            src = getattr(__import__(mod, fromlist=[cls]), cls)()
            chats = await asyncio.to_thread(src.list_chats)
            return {"chats": [{"id": c.chat_id, "name": c.name, "last_message_at": c.last_message_at,
                               "count": c.count} for c in chats]}
        return run

    async def diag_imessage(_: dict) -> dict:
        """Counts only (rows, decodable %, title kinds, contacts loaded): the engine child holds FDA, nothing else does."""
        from .diag import imessage_stats
        from .sources.contacts import ContactsResolver
        try:
            contacts = ContactsResolver()
        except Exception:
            contacts = None
        return await asyncio.to_thread(imessage_stats, contacts=contacts)

    async def diag_whatsapp(_: dict) -> dict:
        from .diag import whatsapp_stats
        return await asyncio.to_thread(whatsapp_stats)

    async def diag_photos(_: dict) -> dict:
        from .diag import photos_stats
        return await asyncio.to_thread(photos_stats)

    async def save_sources(p: dict) -> dict:
        s = ctx.settings
        if "enabled" in p:
            cur = dict(s.get("sources_enabled"))
            cur.update({k: bool(v) for k, v in p["enabled"].items() if k in SOURCE_KEYS})
            s.set("sources_enabled", cur)
        for src, key in (("imessage_excluded", "imessage_excluded"), ("whatsapp_picked", "whatsapp_picked"),
                         ("folders", "folders")):
            if src in p:
                s.set(key, [str(x) for x in p[src]])
        if any(k in p for k in ("enabled", "imessage_excluded", "whatsapp_picked")):
            _reload_chat_sources()
        return {"ok": True}

    def _reload_chat_sources() -> None:
        """Apply a changed chat selection now: rebuild the two chat readers and read them again (live phase only;
        during first setup the build discovers everything itself)."""
        if s_phase() != "live":
            return
        from . import pipeline
        fresh = pipeline.build_live_sources(ctx.settings)
        for name in ("imessage", "whatsapp"):
            if name in fresh:
                ctx.sources[name] = fresh[name]
                ctx.queue.enqueue("discover", payload={"source": name})
            else:
                ctx.sources.pop(name, None)

    def s_phase() -> str:
        return ctx.settings.get("phase")

    async def chat_selection(_: dict) -> dict:
        """What the Settings chat picker starts from."""
        en = ctx.settings.get("sources_enabled")
        return {"imessage_excluded": list(ctx.settings.get("imessage_excluded")),
                "whatsapp_picked": list(ctx.settings.get("whatsapp_picked")),
                "enabled": {"imessage": bool(en.get("imessage")), "whatsapp": bool(en.get("whatsapp"))}}

    async def measure_gmail(_: dict) -> dict:
        from .sources import gmail
        res = await asyncio.to_thread(gmail.measure, lambda: google_auth.service("gmail"),
                                      30, int(ctx.settings.get("gmail_months")), ctx.clock,
                                      allow_domains=ctx.settings.get("mail_allow_domains"),
                                      cache_path=ctx.paths.home / "mail_verdicts.json")
        ctx.settings.set("gmail_measure", res)
        return res

    def _estimate(limit: int | None):
        m = ctx.settings.get("gmail_measure")
        extra = avg = 0
        if m and ctx.settings.get("sources_enabled").get("gmail"):
            have = ctx.con.execute("SELECT COUNT(*) FROM items WHERE source='gmail'").fetchone()[0]
            extra, avg = max(0, int(m["threads"]) - have), float(m["avg_tokens"])
        return estimate(ctx, limit, extra_mail_threads=extra, mail_avg_tokens=avg)

    async def est(p: dict) -> dict:
        return _estimate(p.get("limit")).as_dict()

    async def start_build(p: dict) -> dict:
        limit = p.get("limit")
        if p.get("dry_run"):
            return {"estimate": _estimate(limit).as_dict()}
        if p.get("user_approved") is True and not ctx.paid_allowed():
            # the app's "Start the build" click, after the estimate and caps were shown, is Isaac's yes
            ctx.settings.set("paid_calls_allowed", True)
            log_event("PAID_LOCK_APP_BUTTON", unlocked=True)
        if not ctx.paid_allowed():
            raise Fatal("PAID_LOCKED")
        if state["task"] and not state["task"].done():
            raise Fatal("BUILD_RUNNING")
        b = Build(ctx)
        bid = b.start(limit)

        async def run() -> None:
            ctx.extras = {**(ctx.extras or {}), "build_running": True}
            try:
                await b.run(resume=True)
            finally:
                ctx.extras["build_running"] = False
                if state["progress"]:
                    state["progress"].cancel()
                if ctx.notify:
                    await ctx.notify("build.progress", b.status() or {})
        state["task"] = asyncio.create_task(run())
        state["progress"] = asyncio.create_task(_progress())
        return {"build_id": bid}

    async def raise_cap(p: dict) -> dict:
        scope, usd = p.get("scope"), float(p.get("usd", 0))
        if scope not in ("build", "daily") or usd <= 0:
            raise Fatal("BAD_PARAMS")
        ctx.settings.set("cap_build_usd" if scope == "build" else "cap_daily_usd", usd)
        n = ctx.queue.release("waiting_cap")
        return {"released": n}

    async def build_status(_: dict) -> dict:
        return {"build": Build(ctx).status()}

    async def sg_list(p: dict) -> dict:
        from . import suggest
        return {"suggestions": suggest.list_cards(ctx.con, p.get("status", "new"), ctx.clock)}

    async def sg_go(p: dict) -> dict:
        """Tap Go on a card: plan it if needed, then do it (calendar, reminder, draft, or a Stream task)."""
        from . import prep
        if not isinstance(p.get("id"), str):
            raise Fatal("BAD_PARAMS")
        return await prep.go(ctx, p["id"])

    async def sg_prep(p: dict) -> dict:
        """Plan open cards now (Isaac asked, so it runs under his approval even with background calls locked)."""
        from . import prep
        from .openrouter import user_approved
        ids = [p["id"]] if isinstance(p.get("id"), str) else prep.pending(ctx.con, 20)
        out = {}
        with user_approved():
            for sid in ids:
                out[sid] = (await prep.make_plan(ctx, sid))["plan"]
        return {"planned": out}

    async def sg_act(p: dict) -> dict:
        from . import suggest
        try:
            suggest.act(ctx.con, str(p["id"]), str(p["action"]), p.get("until"), ctx.clock)
        except (KeyError, ValueError):
            raise Fatal("BAD_PARAMS")
        if ctx.notify:
            from .api import status_snapshot
            await ctx.notify("status.changed", status_snapshot(ctx))
        return {"ok": True}

    SETTABLE = {"cap_build_usd": (0.0, 50.0), "cap_daily_usd": (0.0, 50.0), "paused": None,
                "sources_enabled": None, "folders": None, "imessage_excluded": None, "whatsapp_picked": None,
                "gcal_calendars": None, "user_name": None, "mail_allow_domains": None}

    async def settings_add_folders(p: dict) -> dict:
        """Settings > Sources > Folders: add folders he picked (merged, never replacing the ones already there)."""
        import os
        new = [os.path.realpath(str(x)) for x in (p.get("paths") or []) if isinstance(x, str) and os.path.isdir(str(x))]
        if not new:
            raise Fatal("BAD_VALUE")
        cur = [str(x) for x in (ctx.settings.get("folders") or [])]
        have = {os.path.realpath(os.path.expanduser(x)) for x in cur}
        home = os.path.expanduser("~")
        out = list(cur)
        for x in new:
            if x not in have:
                have.add(x)
                out.append("~" + x[len(home):] if x.startswith(home + "/") else x)
        ctx.settings.set("folders", out)
        return {"ok": True, "folders": out}

    async def settings_set(p: dict) -> dict:
        key = p.get("key")
        if key not in SETTABLE:
            raise Fatal("SETTING_NOT_ALLOWED")
        value = p.get("value")
        rng = SETTABLE[key]
        if rng is not None:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not rng[0] < value <= rng[1]:
                raise Fatal("BAD_VALUE")
        ctx.settings.set(key, value)
        return {"ok": True}

    async def wake_get(_: dict) -> dict:
        """The wake word setting, with any key a stored older value lacks filled from the defaults."""
        from .config import DEFAULTS
        return {**DEFAULTS["wake_word"], **(ctx.settings.get("wake_word") or {})}

    async def wake_set(p: dict) -> dict:
        """Change some of it: {enabled?: bool, word?: str, sensitivity?: 0..1}. Anything else, or a bad value, is refused."""
        cur = await wake_get({})
        new = dict(cur)
        for k, v in p.items():
            if k == "enabled" and isinstance(v, bool):
                new[k] = v
            elif k == "word" and isinstance(v, str) and v.strip() and len(v.strip()) <= 32:
                new[k] = v.strip().lower()
            elif k == "sensitivity" and isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0:
                new[k] = float(v)
            else:
                raise Fatal("BAD_VALUE")
        ctx.settings.set("wake_word", new)
        return new

    async def retry_failed(_: dict) -> dict:
        """Requeue real failures. Skipped ones (locked, signed out) would only fail again, and when one item failed twice
        only the newest goes back in line: two active jobs for one item is not allowed."""
        from .api import SKIPPED
        now = ctx.clock.now()
        retry: list[str] = []
        stale: list[str] = []
        seen: set[tuple[str, str]] = set()
        for r in ctx.con.execute("SELECT id, kind, item_id, error_code FROM jobs WHERE status='failed' "
                                 "ORDER BY updated_at DESC, id DESC").fetchall():
            if r["error_code"] in SKIPPED:
                continue
            key = (r["kind"], r["item_id"])
            if r["item_id"] is not None:
                if key in seen or ctx.con.execute("SELECT 1 FROM jobs WHERE kind=? AND item_id=? "
                                                  "AND status NOT IN ('done','failed')", key).fetchone():
                    stale.append(r["id"])
                    continue
                seen.add(key)
            retry.append(r["id"])
        for jid in retry:
            ctx.con.execute("UPDATE jobs SET status='queued', attempts=0, error_code=NULL, next_run_at=?, updated_at=? "
                            "WHERE id=?", (now, now, jid))
        for jid in stale:
            ctx.con.execute("UPDATE jobs SET error_code='SUPERSEDED', updated_at=? WHERE id=?", (now, jid))
        return {"retried": len(retry)}

    async def delete_all(_: dict) -> dict:
        import shutil
        ctx.con.execute("UPDATE jobs SET status='failed', error_code='DELETED' WHERE status IN "
                        "('queued','running','waiting_network','waiting_cap')")
        ctx.con.commit()
        ctx.settings.set("paused", True)
        ctx.con.close()
        shutil.rmtree(ctx.paths.home, ignore_errors=True)
        keychain.delete_all()
        stop = (ctx.extras or {}).get("stop")
        if stop is not None:
            asyncio.get_running_loop().call_later(0.5, stop.set)       # let the reply flush first
        return {"deleted": True}

    return {
        "wake.get": wake_get, "wake.set": wake_set,
        "settings.set": settings_set, "settings.add_folders": settings_add_folders, "jobs.retry_failed": retry_failed, "data.delete_all": delete_all,
        "suggestions.list": sg_list, "suggestions.act": sg_act, "suggestions.go": sg_go, "suggestions.prep": sg_prep,
        "setup.test_openrouter": test_openrouter, "setup.check_fda": check_fda,
        "setup.google_auth_start": google_auth_start, "setup.google_import_client": import_client,
        "setup.set_openrouter_key": set_key, "setup.saved_state": saved_state,
        "setup.list_imessage_chats": _chats("misty_engine.sources.imessage.IMessageSource"),
        "setup.list_whatsapp_chats": _chats("misty_engine.sources.whatsapp.WhatsAppSource"),
        "setup.chat_selection": chat_selection, "diag.imessage": diag_imessage, "diag.whatsapp": diag_whatsapp, "diag.photos": diag_photos,
        "setup.save_sources": save_sources, "setup.measure_gmail": measure_gmail, "setup.estimate": est,
        "setup.start_build": start_build, "control.raise_cap": raise_cap, "build.status": build_status,
    }
