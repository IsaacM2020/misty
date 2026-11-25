"""The live loop's clock: a table of (name, next_at, fn) ticked once a second. Every schedule is Asia/Singapore."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import feedback, suggest
from .clock import next_sgt_midnight, next_sgt_time
from .ctx import Ctx
from .logs import log_event

Fn = Callable[[], Awaitable[None] | None]


@dataclass
class Entry:
    name: str
    next_at: float
    fn: Fn
    every: float | None = None                        # seconds, or None for a calendar rule
    rule: Callable[[float], float] | None = None      # ts -> next ts


class Scheduler:
    def __init__(self, ctx: Ctx, *, watchers=None):
        self.ctx = ctx
        self.watchers = watchers
        self.entries: dict[str, Entry] = {}
        now = ctx.clock.now()

        def every(name: str, s: float, fn: Fn, first: float | None = None) -> None:
            self.entries[name] = Entry(name, now + (s if first is None else first), fn, every=s)

        def rule(name: str, fn: Fn, r: Callable[[float], float]) -> None:
            self.entries[name] = Entry(name, r(now), fn, rule=r)

        every("poll_gmail", 60, lambda: self._poll("gmail"))
        every("poll_gcal", 60, lambda: self._poll("gcal"))
        every("recheck_access", 30, self._recheck_access)
        every("release_snoozes", 60, lambda: suggest.release_snoozes(ctx.con, ctx.clock))
        every("probe_net", 15, self._probe)
        every("release_capped", 60, self._release_capped)
        every("index_md", 60, self._index)
        every("status_push", 2, self._push_status)
        every("watchers", 1, self._drain_watchers)
        every("live_compile", 300, self._live_compile, first=60)
        every("resolve", 60, self._resolve)
        every("summarise", 300, self._summarise, first=120)
        every("prep_cards", 60, self._prep_cards, first=30)
        every("follows", 60, self._follows, first=45)
        every("timers", 5, self._timers, first=7)
        every("routines", 60, self._routines, first=12)
        every("gate_release", 5, lambda: self._gate(), first=10)
        every("ignored_sweep", 600, lambda: feedback.sweep_ignored(ctx), first=300)
        every("learn", 60, self._learn, first=50)
        every("bite", 60, self._bite, first=40)
        every("phrases_prune", 3600, lambda: __import__("misty_engine.phrases", fromlist=["prune"]).prune(ctx), first=120)
        every("paper", 60, self._paper, first=50)
        every("brief", 60, self._brief, first=20)
        every("now_calendar", 120, self._now_calendar, first=10)
        rule("daily_reset", self._daily_reset, next_sgt_midnight)
        rule("health", lambda: ctx.queue.enqueue("health", payload={}), lambda t: next_sgt_time(t, 3, 0))
        rule("priorities_weekly", lambda: ctx.queue.enqueue("priorities_weekly", payload={}),
             lambda t: next_sgt_time(t, 20, 0, weekday=6))
        rule("about_weekly", lambda: ctx.queue.enqueue("about_card", payload={}, cloud=True),
             lambda t: next_sgt_time(t, 20, 10, weekday=6))
        rule("weekly_mutes", lambda: suggest.weekly_mutes(ctx.con, ctx.clock),
             lambda t: next_sgt_time(t, 20, 5, weekday=6))
        self._last_status = None

    # ---- actions
    def _follows(self) -> None:
        """Due follows are checked in the background, one batch at a time (a check can take a few seconds)."""
        from . import follows
        task = getattr(self, "_follow_task", None)
        if task is None or task.done():
            self._follow_task = asyncio.create_task(follows.run_due(self.ctx))

    def _timers(self) -> None:
        """A timer that has run out is handed to the gate (D2)."""
        from . import timers
        task = getattr(self, "_timer_task", None)
        if task is None or task.done():
            self._timer_task = asyncio.create_task(timers.fire_due(self.ctx))

    def _routines(self) -> None:
        """Routines whose time or event just came are offered (or run, if he made them automatic) through the gate (D9)."""
        from . import protocols
        task = getattr(self, "_routine_task", None)
        if task is None or task.done():
            self._routine_task = asyncio.create_task(protocols.fire_due(self.ctx))

    def _brief(self) -> None:
        """The first time he is at the Mac in the morning, today's brief is written and handed to the gate."""
        from . import brief
        task = getattr(self, "_brief_task", None)
        if task is None or task.done():
            self._brief_task = asyncio.create_task(brief.ensure_today(self.ctx))

    def _now_calendar(self) -> None:
        """Today and the next 14 days of his calendar, read once into the cache the Now block and the calendar tool share."""
        from . import now
        c = self.ctx
        if not c.net.online:
            return
        st = c.con.execute("SELECT health FROM sources_state WHERE source='gcal'").fetchone()
        if st and st["health"] == "needs_auth":
            return
        task = getattr(self, "_now_task", None)
        if task is None or task.done():
            self._now_task = asyncio.create_task(now.refresh(c))

    def _bite(self) -> None:
        """The daily study bite of each active course, and a plan that bends when he misses days (E15). One run at a time."""
        from .teach import bite
        task = getattr(self, "_bite_task", None)
        if task is None or task.done():
            self._bite_task = asyncio.create_task(bite.tick(self.ctx))

    def _paper(self) -> None:
        """A timed paper whose clock ran out and that he did not come back to is marked, and he is told (E13). One run at a time."""
        from .teach import paper
        task = getattr(self, "_paper_task", None)
        if task is None or task.done():
            self._paper_task = asyncio.create_task(paper.tick(self.ctx))

    def _learn(self) -> None:
        """Mistakes waiting to teach her something become a lesson in the background, one run at a time."""
        from . import lessons
        task = getattr(self, "_learn_task", None)
        if task is None or task.done():
            self._learn_task = asyncio.create_task(lessons.learn(self.ctx))

    async def _gate(self) -> None:
        """Held items get another look every few seconds; one is told when the moment is right."""
        from . import gate
        await gate.release(self.ctx)

    def _prep_cards(self) -> None:
        """New cards get their Go plan in the background, only while paid calls are allowed (one job at a time)."""
        from . import prep
        c = self.ctx
        if not c.paid_allowed() or not prep.pending(c.con, 1):
            return
        if c.con.execute("SELECT 1 FROM jobs WHERE kind='prep_cards' AND status IN ('queued','running')").fetchone():
            return
        c.queue.enqueue("prep_cards", payload={}, cloud=True)

    def _poll(self, source: str) -> None:
        c = self.ctx
        if not c.net.online or not c.settings.get("sources_enabled").get(source, False):
            return
        st = c.con.execute("SELECT health FROM sources_state WHERE source=?", (source,)).fetchone()
        if st and st["health"] == "needs_auth":
            return
        if source not in c.sources:
            return
        waiting = c.con.execute("SELECT 1 FROM jobs WHERE kind='discover' AND status IN ('queued','running') "
                                "AND json_extract(payload, '$.source')=?", (source,)).fetchone()
        if not waiting:                                    # one pending check per source is enough
            c.queue.enqueue("discover", payload={"source": source})

    def _recheck_access(self) -> None:
        """A chat source that was waiting on Full Disk Access is checked again, so granting it in System Settings
        works without restarting Misty."""
        c = self.ctx
        for name, src in c.sources.items():
            st = c.con.execute("SELECT health FROM sources_state WHERE source=?", (name,)).fetchone()
            if not st or st["health"] != "needs_fda" or not hasattr(src, "check_access") or not src.check_access():
                continue
            if not c.con.execute("SELECT 1 FROM jobs WHERE kind='discover' AND status IN ('queued','running') "
                                 "AND json_extract(payload, '$.source')=?", (name,)).fetchone():
                c.queue.enqueue("discover", payload={"source": name})

    def _resolve(self) -> None:
        """New notes wait for the resolution sweep: one in flight, only when there is something to resolve."""
        c = self.ctx
        if (c.extras or {}).get("build_running"):          # the build runs its own sweep after compile
            return
        if c.con.execute("SELECT 1 FROM jobs WHERE kind='resolve' AND status IN ('queued','running')").fetchone():
            return
        if c.con.execute("SELECT 1 FROM items WHERE status='embedded' LIMIT 1").fetchone():
            c.queue.enqueue("resolve", payload={})

    def _summarise(self) -> None:
        """Pages wait for their summary: one summarise job at a time, only while the day's budget has room."""
        from .wiki import summaries
        c = self.ctx
        if (c.extras or {}).get("build_running"):
            return
        if c.con.execute("SELECT 1 FROM jobs WHERE kind='summarise' AND status IN ('queued','running')").fetchone():
            return
        if summaries._done_today(c) >= summaries.DAILY_MAX:
            return
        if summaries.stale_entities(c, limit=1):
            c.queue.enqueue("summarise", payload={}, cloud=True)

    def _live_compile(self) -> None:
        """Batch what Jev kept (mail, files, calendar) into Haiku calls: one request per batch every 5 min."""
        c = self.ctx
        if (c.extras or {}).get("build_running") or c.settings.get("phase") != "live":
            return
        busy = c.con.execute("SELECT 1 FROM jobs WHERE kind='compile_batch' AND status NOT IN ('done','failed')")
        if busy.fetchone():                                # one sweep in flight at a time (cap, offline, retry)
            return
        from .build import MAX_ITEM_TOKENS, eligible_rows
        from .compile.batching import make_batches
        rows = eligible_rows(c, ("triaged",))
        cool = float(c.settings.get("recompile_cooldown_h") or 0) * 3600
        if rows and cool:                                  # an edited file whose note is fresh waits, so many saves = one call a day
            cooling = {r[0] for r in c.con.execute(
                "SELECT id FROM items WHERE status='triaged' AND note_path IS NOT NULL "
                "AND CAST(json_extract(meta,'$.compiled_at') AS REAL) > ?", (c.clock.now() - cool,))}
            rows = [r for r in rows if r[0] not in cooling]
        if rows and len(rows) < int(c.settings.get("live_compile_min_items")):
            marks = ",".join("?" * len(rows))                 # too few to fill a call: wait, unless the oldest has waited long
            oldest = c.con.execute(f"SELECT MIN(updated_at) FROM items WHERE id IN ({marks})", [r[0] for r in rows]).fetchone()[0]
            if oldest is not None and c.clock.now() - oldest < int(c.settings.get("live_compile_max_wait_min")) * 60:
                return
        for ids in make_batches([(i, min(t, MAX_ITEM_TOKENS)) for i, _, t in rows]):
            c.queue.enqueue("compile_batch", payload={"item_ids": ids}, cloud=True)
        if rows:
            log_event("LIVE_COMPILE", n=len(rows))

    async def _probe(self) -> None:
        c = self.ctx
        if c.net.online:
            return
        if await c.openrouter.probe():
            c.net.set_online(True, "probe")
            c.queue.release("waiting_network")

    def _index(self) -> None:
        """index.md is a deterministic rendering: rewritten (debounced by this 60 s tick) only after a page changed."""
        from .wiki.index_md import write_index_if_dirty
        if self.ctx.wiki is not None:
            write_index_if_dirty(self.ctx)

    CAP_ROOM_USD = 0.002          # a capped purpose restarts once this much of its cap is free (raised, or a new day)

    def _release_capped(self) -> None:
        """Jobs waiting on a cap start again by themselves as soon as that cap has room: the midnight reset still does it
        for everything, but a cap raised in settings (or a purpose cap that has room again) should not wait for midnight."""
        c = self.ctx
        rows = c.con.execute("SELECT DISTINCT error_code AS code FROM jobs WHERE status='waiting_cap' AND error_code LIKE 'CAP_%'").fetchall()
        for r in rows:
            code = r["code"]
            if code.startswith("CAP_PURPOSE:"):
                group = code.split(":", 1)[1]
                sub = c.spend.purpose_cap(group, "live")
                room = (sub[1] - c.spend.spent_purpose(group, "live")) if sub else 1.0
            elif code == "CAP_BUILD":
                continue                                   # the build has its own pause and resume
            else:
                room = float(c.settings.get("cap_daily_usd")) - c.spend.spent_today()
            if room >= self.CAP_ROOM_USD:
                n = c.queue.release_code(code)
                log_event("CAP_ROOM_RELEASE", code=code, n=n)

    def _daily_reset(self) -> None:
        n = self.ctx.queue.release("waiting_cap")
        log_event("DAILY_RESET", n=n)

    async def _push_status(self) -> None:
        if self.ctx.notify is None:
            return
        from .api import status_snapshot
        st = status_snapshot(self.ctx)
        key = repr(st)
        if key != self._last_status:
            self._last_status = key
            await self.ctx.notify("status.changed", st)

    # ---- watchers -> queue (called on the loop thread; FSEvents threads only record paths)
    def _drain_watchers(self) -> None:
        for w in self.watchers or []:
            items = w.flush()
            if not items:
                continue
            if hasattr(w, "source"):                              # chat db watcher: items are source names
                for name in items:
                    if name in self.ctx.sources:
                        self.ctx.queue.enqueue("discover", payload={"source": name})
            else:                                                 # folder watcher: items are paths
                self.ctx.queue.enqueue_paths("discover_paths", [str(x) for x in items])

    # ---- ticking
    async def tick(self) -> list[str]:
        now = self.ctx.clock.now()
        ran = []
        for e in list(self.entries.values()):
            if e.next_at <= now:
                try:
                    r = e.fn()
                    if asyncio.iscoroutine(r):
                        await r
                except Exception as exc:               # one bad action must never stop the loop
                    log_event("SCHED_ERROR", code="ACTION_FAILED", exc=type(exc).__name__)
                e.next_at = e.rule(now) if e.rule else now + (e.every or 60)
                ran.append(e.name)
        return ran

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
