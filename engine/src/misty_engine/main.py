"""Engine process: wires everything together and serves the socket."""
from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from . import keychain
from .api import build_methods
from .clock import Clock
from .config import Pinned, Settings, load_pinned
from .ctx import Ctx
from .db import connect, migrate
from .embed import load_embedder
from .index import Index
from .jev import JevClient
from .jobs import JobQueue
from .logs import log_event, setup_logging
from .net import NetState
from .openrouter import OpenRouterClient
from .runner import ApiRunner
from .paths import MistyPaths
from .rpc import RpcServer
from .spend import SpendMeter
from .wiki.store import WikiStore


def _excepthook(exc_type, exc, tb) -> None:
    try:
        log_event("ENGINE_CRASH", exc=exc_type.__name__)
    except Exception:
        pass
    sys.__stderr__.write(f"engine crashed: {exc_type.__name__}\n")


class Engine:
    def __init__(self, paths: MistyPaths, pinned: Pinned, *, clock: Clock | None = None,
                 socket_path: Path | None = None, parent_pid: int | None = None):
        self.paths, self.pinned = paths, pinned
        self.clock = clock or Clock()
        self.socket_path = socket_path or paths.sock
        self.parent_pid = parent_pid

    def build_ctx(self) -> Ctx:
        con = connect(self.paths.db)
        migrate(con, self.pinned.embedding.dim)
        settings = Settings(con)
        queue = JobQueue(con, self.clock, settings)
        spend = SpendMeter(con, self.clock, settings)
        net = NetState()
        orc = OpenRouterClient(pinned=self.pinned, settings=settings, spend=spend, net=net,
                               key_provider=lambda: keychain.get_secret("openrouter"), clock=self.clock)
        ctx = Ctx(paths=self.paths, pinned=self.pinned, con=con, settings=settings, queue=queue,
                  spend=spend, net=net, clock=self.clock, openrouter=orc,
                  embedder=load_embedder(self.pinned), index=Index(con, self.pinned.embedding.dim))
        ctx.jev = JevClient(orc, self.pinned, ctx.card)
        ctx.wiki = WikiStore(self.paths.wiki, self.clock)
        ctx.wiki.init()
        ctx.runner = ApiRunner(orc, lambda: settings.get("phase"))
        from . import ways
        ways.seed_defaults(ctx)                          # his ways start from the shipped defaults, once
        from . import privacy
        privacy.seed_defaults(ctx)                       # the apps she can't see start from the shipped list, once
        return ctx

    async def run(self) -> None:
        self.paths.ensure()
        setup_logging(self.paths.logs)
        sys.excepthook = _excepthook
        ctx = self.build_ctx()
        recovered = ctx.queue.recover_running()
        from . import classes
        classes.close_stale(ctx.con, ctx.clock)          # a class left open by an app that quit mid-class
        classes.prune(ctx.con, ctx.clock)                # transcripts older than 6 months
        rpc = RpcServer(self.socket_path, {})
        ctx.notify = rpc.notify
        rpc.methods.update(build_methods(ctx))
        await rpc.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        log_event("ENGINE_START", count=recovered)
        ctx.extras = {**(ctx.extras or {}), "stop": stop}
        tasks, watchers = await self._start_live(ctx, stop)
        import os
        if not os.environ.get("MISTY_NO_WARM"):  # tests set this: warming downloads ~1.4 GB into each throwaway home
            tasks.append(asyncio.create_task(self._warm(ctx)))
        from .class_notes import make_notes
        tasks += [asyncio.create_task(make_notes(ctx, sid)) for sid in classes.needs_notes(ctx.con)]
        if self.parent_pid:
            tasks.append(asyncio.create_task(self._watch_parent(stop)))
        await stop.wait()
        for t in tasks:
            t.cancel()
        for w in watchers:
            w.stop()
        await rpc.stop()
        ctx.con.close()
        log_event("ENGINE_STOP")


    async def _warm(self, ctx: Ctx) -> None:
        """Load the embedding model now (~8 s cold) so the first chat message doesn't pay for it."""
        try:
            await asyncio.to_thread(ctx.embedder.embed_query, "warm")
            log_event("EMBED_WARM")
        except Exception:
            pass

    async def _watch_parent(self, stop: asyncio.Event) -> None:
        """Exit within seconds when the app that launched us is gone (no orphan engines)."""
        import os
        while not stop.is_set():
            if os.getppid() != self.parent_pid:
                log_event("PARENT_GONE")
                stop.set()
                return
            await asyncio.sleep(1.0)

    async def _start_live(self, ctx: Ctx, stop: asyncio.Event):
        """Worker + scheduler + watchers. Returns (tasks, watchers) so run() can stop them."""
        import os
        from pathlib import Path
        from .pipeline import Worker, build_live_sources
        from .scheduler import Scheduler
        from .watch import ChatDbWatcher, FolderWatcher
        ctx.sources = build_live_sources(ctx.settings)
        watchers: list = []
        if ctx.settings.get("sources_enabled").get("folders"):
            roots = [Path(os.path.expanduser(p)) for p in ctx.settings.get("folders")]
            excl = [Path(os.path.expanduser(p)) for p in ctx.settings.get("excluded_paths")]
            w = FolderWatcher(roots, excl, clock=self.clock)
            w.start()
            watchers.append(w)
        from .sources.imessage import DEFAULT_DB as IMESSAGE_DB
        from .sources.whatsapp import WA_DB
        # both chat databases are watched from the start: a chat picked later in Settings needs no restart
        for name, default in (("imessage", IMESSAGE_DB), ("whatsapp", WA_DB)):
            src = ctx.sources.get(name)
            cw = ChatDbWatcher(getattr(src, "db_path", default), name, clock=self.clock)
            try:
                cw.start()
                watchers.append(cw)
            except OSError:
                pass
        live_kinds = ["triage", "embed_note", "resolve", "discover_paths",
                      "summarise", "health", "priorities_weekly", "about_card", "teach_map", "teach_plan", "teach_bank", "teach_slip_label", "teach_paper_parse"]
        build_kinds = ["discover", "prep", "compile_batch"]

        def kinds() -> list[str]:
            ks = live_kinds + (build_kinds if ctx.settings.get("phase") == "live" else [])
            if (ctx.extras or {}).get("build_running"):      # the build's own worker owns these while it runs
                ks = [k for k in ks if k not in ("embed_note", "resolve", "compile_batch", "summarise")]
            return ks
        conc = {"discover": 1, "prep": 2, "triage": 4,
                "embed_note": 1, "resolve": 1, "discover_paths": 1, "summarise": 1, "about_card": 1, "health": 1, "compile_batch": 2,
                "priorities_weekly": 1, "teach_map": 1, "teach_plan": 1, "teach_bank": 1, "teach_slip_label": 1, "teach_paper_parse": 1}
        worker = Worker(ctx, conc)
        sched = Scheduler(ctx, watchers=watchers)
        tasks = [asyncio.create_task(worker.run_forever(stop, kinds)),
                 asyncio.create_task(sched.run_forever(stop))]
        if ctx.settings.get("phase") == "live":
            for name in ctx.sources:                       # catch up on whatever changed while the engine was off
                ctx.queue.enqueue("discover", payload={"source": name})
        return tasks, watchers


def take_lock(paths: MistyPaths):
    """One engine per ~/Misty. The lock lives as long as the returned file object."""
    import fcntl
    paths.ensure()
    f = open(paths.run / "engine.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def serve(socket: str | None = None, parent_pid: int | None = None) -> int:
    import os
    paths = MistyPaths.from_env()
    lock = take_lock(paths)
    if lock is None:
        sys.stderr.write("ENGINE_ALREADY_RUNNING\n")
        return 3
    if parent_pid or (paths.models / "hub").exists():
        os.environ["HF_HOME"] = str(paths.models)           # models live inside ~/Misty so Delete-all removes them
    asyncio.run(Engine(paths, load_pinned(), socket_path=Path(socket) if socket else None,
                       parent_pid=parent_pid).run())
    return 0
