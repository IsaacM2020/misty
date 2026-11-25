"""`misty` command line."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import getpass
import json
import sys
from pathlib import Path

from . import keychain
from .paths import MistyPaths


async def _rpc(sock: str, method: str, params: dict | None = None) -> dict:
    reader, writer = await asyncio.open_unix_connection(sock)
    writer.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode() + b"\n")
    await writer.drain()
    loop = asyncio.get_running_loop()
    end = loop.time() + 10
    try:
        while True:                                   # skip broadcast notifications (no id) until our reply arrives
            line = await asyncio.wait_for(reader.readline(), timeout=max(end - loop.time(), 0.01))
            if not line:
                raise ConnectionError("engine closed the socket")
            msg = json.loads(line)
            if msg.get("id") == 1:
                return msg
    finally:
        writer.close()


def _wa_import(path: str) -> int:
    from .config import Settings, load_pinned
    from .db import connect, migrate
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        print("file not found", file=sys.stderr)
        return 1
    paths = MistyPaths.from_env()
    paths.ensure()
    con = connect(paths.db)
    migrate(con, load_pinned().embedding.dim)
    st = Settings(con)
    cur = st.get("whatsapp_exports")
    if str(p) not in cur:
        st.set("whatsapp_exports", cur + [str(p)])
    print(f"stored; run: misty index --source whatsapp_export")
    return 0


def _search(text: str, sources: list[str] | None, when: str, n: int) -> int:
    import time
    from .config import load_pinned
    from .main import Engine
    from .search import search
    paths = MistyPaths.from_env()
    paths.ensure()
    ctx = Engine(paths, load_pinned()).build_ctx()
    t = time.perf_counter()
    ctx.embedder.embed_query("warm up")        # the engine keeps the model loaded; the CLI pays for it once
    load_ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    hits = search(ctx, text, sources=sources, when=when, limit=n)
    ms = (time.perf_counter() - t) * 1000
    for i, h in enumerate(hits, start=1):
        print(f"{i:>2}. {h.score:.4f}  [{h.source}/{h.kind}] {h.title}")
        print(f"      {h.locator}")
        if h.snippet:
            print(f"      {h.snippet.replace(chr(10), ' ')[:160]}")
    print(f"latency_ms={ms:.0f} (one-time model load in this process: {load_ms:.0f} ms)")
    return 0


def _index(source: str | None, limit: int | None, include_images: bool = False) -> int:
    from .main import Engine
    from .config import load_pinned
    from .logs import setup_logging
    from .pipeline import format_report, run_index
    paths = MistyPaths.from_env()
    paths.ensure()
    setup_logging(paths.logs)
    ctx = Engine(paths, load_pinned()).build_ctx()
    ctx.queue.recover_running()
    rep = asyncio.run(run_index(ctx, source, limit, include_images))
    print(format_report(rep))
    return 0


def _adopt_models() -> int:
    """Move (not copy) Misty's own model folders from the shared HF cache into ~/Misty/models/hub."""
    import shutil
    from .config import load_pinned
    pinned = load_pinned()
    paths = MistyPaths.from_env()
    src = Path.home() / ".cache" / "huggingface" / "hub"
    dst = paths.models / "hub"
    dst.mkdir(parents=True, exist_ok=True)
    ids = [pinned.embedding.model_id]
    for mid in ids:
        name = "models--" + mid.replace("/", "--")
        if (src / name).is_dir() and not (dst / name).exists():
            shutil.move(str(src / name), str(dst / name))
            print(f"moved {name}")
        else:
            print(f"skipped {name}")
    return 0


def _ctx():
    from .config import load_pinned
    from .logs import setup_logging
    from .main import Engine
    paths = MistyPaths.from_env()
    paths.ensure()
    setup_logging(paths.logs)
    ctx = Engine(paths, load_pinned()).build_ctx()
    ctx.queue.recover_running()
    return ctx


def _picks(action: str | None, pick_id: str | None) -> int:
    """B2b: `misty picks` lists the new ones; `add <id>` appends it to ~/Misty/checks/toolpick.yaml (after a backup); `drop <id>`."""
    from . import wrong_picks
    from .db import connect
    paths = MistyPaths.from_env()
    con = connect(paths.db, load_vec=False)
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='wrong_picks'").fetchone() is None:
        print("no wrong picks yet (the log appears the first time the new engine starts)")
        return 0
    if action is None:
        rows = wrong_picks.waiting(con)
        if not rows:
            print("no wrong picks waiting")
        for r in rows:
            print(f"{r['id']}  said: {r['said']!r}\n    she did: {r['picked_tool']}   should have: {r['wanted_tool']}"
                  + (f"   (he said: {r['note']!r})" if r["note"] else ""))
        return 0
    if not pick_id:
        print(f"picks {action} needs an id", file=sys.stderr)
        return 2
    if action == "add":
        r = wrong_picks.add_to_set(con, pick_id, paths.home / "checks" / "toolpick.yaml", paths.home / "backup")
        print(f"added to ~/Misty/checks/toolpick.yaml (backup: {r['backup']})" if r["ok"] else r["error"])
    else:
        r = wrong_picks.dismiss(con, pick_id)
        print("dropped" if r["ok"] else r["error"])
    return 0 if r["ok"] else 1


def _paid(action: str) -> int:
    ctx = _ctx()
    ctx.settings.set("paid_calls_allowed", action == "unlock")
    from .logs import log_event
    log_event("PAID_LOCK", unlocked=(action == "unlock"))
    print("paid calls ALLOWED" if action == "unlock" else "paid calls LOCKED")
    return 0


def _google(action: str, path: str | None) -> int:
    from .errors import MistyError
    from .sources import google_auth
    try:
        if action == "import-client":
            if not path or not Path(path).expanduser().is_file():
                print("give the path of the downloaded client_secret JSON", file=sys.stderr)
                return 1
            google_auth.import_client_json(Path(path).expanduser().read_text())
            print("client stored in Keychain. Delete the downloaded file now.")
            return 0
        email = google_auth.begin_auth(open_browser=True)
        ctx = _ctx()
        enabled = dict(ctx.settings.get("sources_enabled"))
        enabled.update({"gmail": True, "gcal": True})
        ctx.settings.set("sources_enabled", enabled)
        print(f"connected as {email}; Gmail and Calendar sources enabled")
        return 0
    except MistyError as e:
        print(f"failed: {e.code}", file=sys.stderr)
        return 1


def _setup(action: str) -> int:
    import json as _json
    from .errors import MistyError
    ctx = _ctx()
    from .api import build_methods
    methods = build_methods(ctx)
    name = {"test-openrouter": "setup.test_openrouter", "measure-gmail": "setup.measure_gmail",
            "estimate": "setup.estimate"}[action]
    try:
        out = asyncio.run(methods[name]({}))
    except MistyError as e:
        print(f"failed: {e.code}", file=sys.stderr)
        return 1
    print(_json.dumps(out, indent=2))
    return 0


def _build(limit: int | None, dry: bool) -> int:
    from .build import Build, estimate, format_estimate
    from .errors import MistyError
    ctx = _ctx()
    if dry:
        from .api_setup import setup_methods
        out = asyncio.run(setup_methods(ctx)["setup.estimate"]({"limit": limit}))
        from .build import Estimate
        print(format_estimate(Estimate(**out)))
        return 0
    if not ctx.paid_allowed():
        print("paid calls are locked (misty paid unlock after Isaac says yes)", file=sys.stderr)
        return 2
    print(format_estimate(estimate(ctx, limit)))

    async def go():
        b = Build(ctx)
        task = asyncio.create_task(b.run(limit))
        while not task.done():
            await asyncio.sleep(5)
            st = b.status()
            if st:
                print(f"[{st['phase']}] {st['done']}/{st['total']} failed={st['failed']} "
                      f"spent=${st['spent_usd']:.4f}", flush=True)
        return await task
    try:
        st = asyncio.run(go())
    except MistyError as e:
        print(f"build stopped: {e.code}", file=sys.stderr)
        return 1
    print(f"build {st['phase']}: {st['done']}/{st['total']} compiled, failed={st['failed']}, "
          f"spent=${st['spent_usd']:.4f}, paused_by={st['paused_by']}")
    return 0


def _call(method: str, params: dict) -> int:
    import json as _json
    from .api import build_methods
    from .errors import MistyError
    ctx = _ctx()
    try:
        out = asyncio.run(build_methods(ctx)[method](params))
    except MistyError as e:
        print(f"failed: {e.code}", file=sys.stderr)
        return 1
    if method == "ask":
        print(out["answer"])
        if out["citations"]:
            print("cites: " + ", ".join(out["citations"]))
        if out["filed"]:
            print(f"filed: {out['filed']}")
    elif method == "context":
        print(out["text"])
        print(f"\n[{out['tokens']} tokens, {len(out['pages'])} pages]")
    else:
        print(_json.dumps(out, indent=2, default=str))
    return 0


def _exam(file: str, out: str, label: str, only: str | None, via: str = "wiki") -> int:
    from pathlib import Path
    from .exam import load_questions, run_exam, write_reports
    qs = load_questions(Path(file))
    if not qs:
        print("no questions found in " + file, file=sys.stderr)
        return 1
    ids = [int(x) for x in only.split(",")] if only else None
    ctx = _ctx()
    result = asyncio.run(run_exam(ctx, qs, ids, via))
    jp, mp = write_reports(result, Path(out), label)
    errs = [r["n"] for r in result["rows"] if r["error"]]
    print(f"asked {len(result['rows'])} question(s), cost ${result['total_usd']:.4f}, errors on: {errs or 'none'}")
    print(f"wrote {mp}\nwrote {jp}")
    return 1 if errs else 0


def _synth(dry: bool) -> int:
    from .wiki import synth
    ctx = _ctx()
    from .pipeline import Worker
    est = synth.estimate_calls(ctx)
    print("core jobs plan: " + ", ".join(f"{k}={v}" for k, v in est.items()))
    if dry:
        return 0
    kinds = list(synth.STEPS) + ["synth_index"]

    async def go():
        ctx.queue.enqueue("synth_areas", payload={})
        w = Worker(ctx, {k: 1 for k in kinds})
        while True:
            await w.run_until_idle(kinds)
            held = ctx.con.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='waiting_cap' AND kind LIKE 'synth%'").fetchone()[0]
            if held:
                print(f"{held} step(s) wait for the next budget window (a spend cap was reached)", flush=True)
            break
    asyncio.run(go())
    failed = ctx.con.execute("SELECT COUNT(*) FROM jobs WHERE status='failed' AND kind LIKE 'synth%'").fetchone()[0]
    print(f"core jobs done, failed steps={failed}")
    return 0 if not failed else 1


class _CappedRun:
    """A one-off paid job (a migration step) with its own build id and its own dollar cap, restored afterwards."""

    def __init__(self, ctx, max_usd: float, label: str):
        self.ctx, self.max_usd, self.label = ctx, max_usd, label
        self.build_id = f"{label}-{int(ctx.clock.now())}"

    def __enter__(self):
        self.old = self.ctx.settings.get("cap_build_usd")
        self.ctx.settings.set("cap_build_usd", self.max_usd)
        return self

    def __exit__(self, *exc):
        self.ctx.settings.set("cap_build_usd", self.old)

    def spent(self) -> float:
        return self.ctx.spend.spent_build(self.build_id)


def _registry(dry: bool, apply: bool, max_usd: float | None = None) -> int:
    from .errors import CapHit, NetworkDown
    from .registry.rebuild import rebuild
    if dry == apply:
        print("say exactly one of --dry-run or --apply", file=sys.stderr)
        return 2
    ctx = _ctx()
    if apply and not ctx.paid_allowed():
        print("--apply asks the judge model (paid): unlock with `misty paid unlock` and approve the dry run first",
              file=sys.stderr)
        return 2
    if dry:
        rep = asyncio.run(rebuild(ctx, dry_run=True, cache_only=True))
        print(json.dumps(rep.counts(), indent=2, sort_keys=True))        # counts only
        return 0
    if max_usd is None:
        print("--apply needs --max-usd (a hard cap for this step)", file=sys.stderr)
        return 2
    with _CappedRun(ctx, max_usd, "migrate") as run:
        try:
            rep = asyncio.run(rebuild(ctx, dry_run=False, cache_only=False, build_id=run.build_id))
        except (CapHit, NetworkDown) as e:
            print(f"stopped by {e.code} after ${run.spent():.4f}; the registry is consistent, run it again to resume",
                  file=sys.stderr)
            return 1
        out = rep.counts()
        out["spent_usd"] = round(run.spent(), 6)
        print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def _summarise(max_usd: float) -> int:
    from .errors import CapHit, NetworkDown
    from .wiki.summaries import summarise_stale
    ctx = _ctx()
    if not ctx.paid_allowed():
        print("this step asks the summary model (paid): unlock with `misty paid unlock` first", file=sys.stderr)
        return 2
    with _CappedRun(ctx, max_usd, "summ") as run:
        try:
            n = asyncio.run(summarise_stale(ctx, build_id=run.build_id))
        except (CapHit, NetworkDown) as e:
            print(f"stopped by {e.code} after ${run.spent():.4f}; run it again to continue", file=sys.stderr)
            return 1
        print(json.dumps({"summarised": n, "spent_usd": round(run.spent(), 6)}, sort_keys=True))
    return 0


def _areas(action: str, args: list[str], plan: bool, force: bool) -> int:
    from .errors import MistyError
    from .wiki import areas as A
    ctx = _ctx()
    try:
        if action == "discover":
            if plan:
                print("areas plan: " + ", ".join(f"{k}={v}" for k, v in A.plan(ctx).items()))
                return 0
            out = asyncio.run(A.discover(ctx, force=force))
            print(f"synthesized {out['areas']} areas, {int(out['coverage'] * 100)}% of notes placed "
                  f"(digest {out['digest_tokens']} tokens)")
            for pv in out["preview"]:
                print(f"- {pv['name']} [{pv['notes']} notes, importance {pv['importance']}]: {pv['description']}")
                print(f"    why: {pv['evidence']}")
                print(f"    members: {', '.join(pv['members'])}")
            for g in out["gaps"]:
                print(f"  gap: {g}")
            return 0
        elif action == "rename" and len(args) == 2:
            print("renamed to", A.rename_area(ctx, args[0], args[1]))
        elif action == "merge" and len(args) == 2:
            A.merge_areas(ctx, args[0], args[1])
        elif action == "drop" and len(args) == 1:
            A.drop_area(ctx, args[0])
        elif action == "accept":
            print(f"accepted {A.accept_all(ctx)} areas")
        elif action == "graph":
            from .wiki.graph import build_graph
            g = build_graph(ctx, user_label=ctx.settings.get("user_name") or "")
            kinds = Counter(n["type"] for n in g["nodes"])
            print(f"nodes={len(g['nodes'])} edges={len(g['edges'])} by type={dict(kinds)}")
            return 0
        elif action != "show":
            print("usage: areas rename SLUG NAME | merge KEEP DROP | drop SLUG", file=sys.stderr)
            return 2
        thin = {t["slug"]: t for t in A.thin_report(ctx)}
        for a in A.read_areas(ctx):
            t = thin.get(a["slug"], {})
            print(f"[{a['importance']}] {a['slug']}  ({a['status']}, {a['notes']} notes)  {a['title']}")
            print(f"      {a['description']}")
            print(f"      people in {int(t.get('people_share', 0) * 100)}% of notes, "
                  f"dated lines in {int(t.get('dated_share', 0) * 100)}%, sources {t.get('sources', {})}")
        for g in ctx.settings.get("area_gaps"):
            print(f"  gap: {g}")
        return 0
    except MistyError as e:
        print(f"failed: {e.code}", file=sys.stderr)
        return 1


def _eval_suites(suite: str, cheap: bool, yes: bool, path: str | None) -> int:
    """The Q5 harness: free suites run at once; answers spend (about $0.0005 a call), so it prints the estimate
    and waits for --yes."""
    from . import evaljudge, evalset
    from .errors import MistyError
    p = Path(path) if path else Path(__file__).resolve().parents[3] / "tests" / "golden.yaml"
    if not p.is_file():
        print(f"no golden set at {p}", file=sys.stderr)
        return 1
    try:
        cases = evalset.load(p)
    except MistyError as e:
        print(f"bad golden file: {e.code}", file=sys.stderr)
        return 1
    suites = ("retrieval", "pack") if suite in ("retrieval", "pack") else ("answers",)
    if "answers" in suites:
        n = sum(1 for c in cases if c.kind == "ask" and c.reference)
        calls = n * (6 if cheap else 4)
        print(f"{n} golden questions, about {calls} calls, about ${calls * 0.0005:.3f} (cap $0.25)")
        if not yes:
            print("add --yes to run it")
            return 2
    ctx = _ctx()
    rep = asyncio.run(evaljudge.run_suites(ctx, cases, suites, cheap=cheap))
    print(evaljudge.format_report(rep))
    return 0 if not rep.incomplete else 1


def _eval(kind: str | None, path: str | None) -> int:
    from . import evalset
    from .errors import MistyError
    p = Path(path) if path else Path(__file__).resolve().parents[3] / "tests" / "golden.yaml"
    if not p.is_file():
        print(f"no golden set at {p} (copy tests/golden.example.yaml and write 30 queries)", file=sys.stderr)
        return 1
    try:
        cases = evalset.load(p)
    except MistyError as e:
        print(f"bad golden file: {e.code}", file=sys.stderr)
        return 1
    n_ask = sum(1 for c in cases if c.kind == "ask" and kind in (None, "ask"))
    if n_ask:
        print(f"{n_ask} ask cases use Haiku through OpenRouter: about ${n_ask * 0.002:.3f} in total")
    ctx = _ctx()
    ctx.embedder.embed_query("warm up")
    try:
        rep = asyncio.run(evalset.run(ctx, cases, kind))
    except MistyError as e:
        print(f"eval stopped: {e.code}", file=sys.stderr)
        return 1
    print(evalset.format_report(rep))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="misty")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the engine")
    s.add_argument("--socket")
    s.add_argument("--parent-pid", type=int)
    md = sub.add_parser("models", help="model files")
    md.add_argument("action", choices=["adopt"])
    sub.add_parser("status", help="print engine status")
    se = sub.add_parser("search", help="hybrid search over the local index")
    se.add_argument("text")
    se.add_argument("--source", nargs="+", dest="sources",
                    choices=["files", "photos", "mail", "calendar", "imessage", "whatsapp", "claude_mem"])
    se.add_argument("--when", default="any", choices=["any", "today", "week", "month", "year", "older"])
    se.add_argument("-n", type=int, default=10)
    ix = sub.add_parser("index", help="index local sources (discover, extract, scrub, chunk, embed)")
    ix.add_argument("--source", choices=["folders", "claude_mem", "whatsapp_export", "gmail", "gcal"])
    wa = sub.add_parser("whatsapp", help="WhatsApp helpers")
    wa.add_argument("action", choices=["import-export"])
    wa.add_argument("path")
    ix.add_argument("--limit", type=int)
    ix.add_argument("--images", action="store_true",
                    help="also index images (off by default, decision P26; they embed at ~1.7/s)")
    bd = sub.add_parser("build", help="cloud compile (Jev worth gate + Haiku source notes)")
    bd.add_argument("--limit", type=int)
    bd.add_argument("--dry-run-estimate", action="store_true")
    pk = sub.add_parser("picks", help="wrong tool picks he corrected: list them, add one to his tool-pick set, or drop it")
    pk.add_argument("action", nargs="?", choices=["add", "drop"])
    pk.add_argument("id", nargs="?")
    pd = sub.add_parser("paid", help="paid-call lock")
    pd.add_argument("action", choices=["unlock", "lock"])
    go = sub.add_parser("google", help="Google account (read-only Gmail + Calendar)")
    go.add_argument("action", choices=["import-client", "login"])
    go.add_argument("path", nargs="?")
    su = sub.add_parser("setup", help="setup checks")
    su.add_argument("action", choices=["test-openrouter", "measure-gmail", "estimate"])
    ak = sub.add_parser("ask", help="ask the wiki (cited answer, Haiku)")
    ak.add_argument("text")
    ex = sub.add_parser("exam", help="Season 2: ask the wiki the exam questions, save answers (not scored, not filed)")
    ex.add_argument("--file", default="~/Misty/exam/season2-exam-draft.md")
    ex.add_argument("--out", default="~/Misty/exam/runs")
    ex.add_argument("--label", default="run")
    ex.add_argument("--only", help="comma-separated question numbers, e.g. 6 or 1,2,3")
    ex.add_argument("--via", choices=["wiki", "chat"], default="chat", help="chat = the real Misty chat, read-only")
    cx = sub.add_parser("context", help="print the context pack for a topic")
    cx.add_argument("topic")
    cx.add_argument("--budget", type=int, default=2000)
    qy = sub.add_parser("query", help="route a request (Jev) and run it")
    qy.add_argument("text")
    ev = sub.add_parser("eval", help="run the golden query set")
    ev.add_argument("--kind", choices=["search", "ask"])
    ev.add_argument("--file")
    ev.add_argument("--suite", choices=["retrieval", "pack", "answers"],
                    help="Q5 harness: retrieval and pack are free, answers spends (needs --yes)")
    ev.add_argument("--cheap", action="store_true", help="answers: also answer with the cheap model")
    ev.add_argument("--yes", action="store_true")
    wk = sub.add_parser("wiki", help="wiki maintenance")
    wk.add_argument("action", choices=["repair", "adopt-legacy"])
    ob = sub.add_parser("onboard", help="one-off onboarding jobs on the API (area descriptions, about card, priorities)")
    ob.add_argument("action", choices=["core", "summarise"])
    ob.add_argument("--max-usd", type=float, default=0.5)
    ob.add_argument("--plan", action="store_true", help="only print the call plan")
    ar = sub.add_parser("areas", help="discover and review the life areas (stage 5 consumes them)")
    ar.add_argument("action", choices=["discover", "show", "rename", "merge", "drop", "accept", "graph"])
    ar.add_argument("args", nargs="*", help="rename SLUG NAME | merge KEEP DROP | drop SLUG")
    ar.add_argument("--plan", action="store_true", help="discover: only print what would be sent and to which model")
    ar.add_argument("--force", action="store_true", help="discover again even if areas were already accepted")
    k = sub.add_parser("keys", help="store a key in the Keychain")
    k.add_argument("action", choices=["set"])
    k.add_argument("name", choices=["openrouter"])
    k.add_argument("--from-clipboard", action="store_true",
                   help="read the key from the macOS clipboard, store it, then clear the clipboard")
    sp = sub.add_parser("spend", help="what the models cost, by purpose")
    sp.add_argument("--by-purpose", action="store_true")
    sp.add_argument("--since", help="YYYY-MM-DD (Singapore time)")
    ac = sub.add_parser("acceptance", help="measure the Part 1 acceptance table on this wiki (counts only)")
    ac.add_argument("--with-answers", action="store_true", help="also run A6 (spends under $0.25)")
    ac.add_argument("--golden")
    ac.add_argument("--dups")
    rg = sub.add_parser("registry", help="entity registry: bring an existing wiki in, counts first")
    rg.add_argument("action", choices=["rebuild"])
    rg.add_argument("--dry-run", action="store_true", help="counts only: nothing is written, no model is called")
    rg.add_argument("--apply", action="store_true", help="really rebuild (paid judge calls; needs paid calls unlocked)")
    rg.add_argument("--max-usd", type=float, help="hard dollar cap for --apply")
    ca = sub.add_parser("class-audio-report", help="read-only counts for one online class's recorded audio and transcript")
    ca.add_argument("session", help="the class session id (the folder name under class-audio/)")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)

    if a.cmd == "class-audio-report":
        from .class_report import report
        text = report(MistyPaths.from_env().home, a.session)
        if text is None:
            print("no such class (no audio folder and nothing saved for that id)", file=sys.stderr)
            return 1
        print(text)
        return 0
    if a.cmd == "serve":
        from .main import serve
        return serve(a.socket, a.parent_pid)
    if a.cmd == "models":
        return _adopt_models()
    if a.cmd == "status":
        try:
            r = asyncio.run(_rpc(str(MistyPaths.from_env().sock), "status.get"))
        except (ConnectionError, FileNotFoundError, OSError):
            print("engine not running", file=sys.stderr)
            return 1
        print(json.dumps(r.get("result", r), indent=2))
        return 0
    if a.cmd == "whatsapp":
        return _wa_import(a.path)
    if a.cmd == "search":
        return _search(a.text, a.sources, a.when, a.n)
    if a.cmd == "index":
        return _index(a.source, a.limit, a.images)
    if a.cmd == "eval":
        if a.suite:
            return _eval_suites(a.suite, a.cheap, a.yes, a.file)
        return _eval(a.kind, a.file)
    if a.cmd == "ask":
        return _call("ask", {"text": a.text})
    if a.cmd == "exam":
        return _exam(a.file, a.out, a.label, a.only, a.via)
    if a.cmd == "context":
        return _call("context", {"topic": a.topic, "budget_tokens": a.budget})
    if a.cmd == "query":
        return _call("query", {"text": a.text})
    if a.cmd == "areas":
        return _areas(a.action, a.args, a.plan, a.force)
    if a.cmd == "spend":
        from datetime import datetime
        from .acceptance import spend_by_purpose
        from .clock import SGT
        ctx = _ctx()
        since = datetime.fromisoformat(a.since).replace(tzinfo=SGT).timestamp() if a.since else 0.0
        print(json.dumps(spend_by_purpose(ctx, since), indent=2, sort_keys=True))
        return 0
    if a.cmd == "acceptance":
        from . import acceptance
        root = Path(__file__).resolve().parents[3] / "tests"
        ctx = _ctx()
        rows = asyncio.run(acceptance.run(ctx, Path(a.golden) if a.golden else root / "golden.yaml",
                                          Path(a.dups) if a.dups else root / "golden.dups.yaml",
                                          with_answers=a.with_answers))
        print(acceptance.format_rows(rows))
        return 0 if all(r.ok for r in rows) else 1
    if a.cmd == "registry":
        return _registry(a.dry_run, a.apply, a.max_usd)
    if a.cmd == "wiki":
        if a.action == "repair":
            from .registry import render
            ctx = _ctx()
            out = render.repair(ctx)
            out["lint"] = render.lint(ctx)
            print(out)                                   # counts only
            return 0
        if a.action == "adopt-legacy":
            from .registry import render
            ctx = _ctx()
            out = render.adopt_legacy_pages(ctx)         # after `registry rebuild --apply`: no model, no cost
            out["lint"] = render.lint(ctx)
            print(out)                                   # counts only
            return 0
    if a.cmd == "onboard":
        if a.action == "summarise":
            return _summarise(a.max_usd)
        return _synth(a.plan)
    if a.cmd == "build":
        return _build(a.limit, a.dry_run_estimate)
    if a.cmd == "picks":
        return _picks(a.action, a.id)
    if a.cmd == "paid":
        return _paid(a.action)
    if a.cmd == "google":
        return _google(a.action, a.path)
    if a.cmd == "setup":
        return _setup(a.action)
    if a.cmd == "keys":
        if a.from_clipboard:
            import subprocess
            value = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
        elif not sys.stdin.isatty():
            value = sys.stdin.read().strip()
        else:
            value = getpass.getpass("OpenRouter key (hidden): ").strip()
        if a.name == "openrouter" and value and not value.startswith("sk-or-"):
            print("that does not look like an OpenRouter key (should start with sk-or-); nothing stored",
                  file=sys.stderr)
            return 1
        if not value:
            print("empty, nothing stored", file=sys.stderr)
            return 1
        keychain.set_secret(a.name, value)
        if a.from_clipboard:
            import subprocess
            subprocess.run(["pbcopy"], input=b"")          # do not leave the key on the clipboard
        print("stored in Keychain" + (" (clipboard cleared)" if a.from_clipboard else ""))
        return 0
    return 2
