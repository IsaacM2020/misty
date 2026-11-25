#!/usr/bin/env python3
"""E19: the scorecard for three weeks of being taught. Free: it reads the tables, makes no model call and writes nothing to the db.

    engine/.venv/bin/python scripts/checks/teach_report.py [--db <misty.db>] [--cap 0.10] [--cites]
    engine/.venv/bin/python scripts/checks/teach_report.py --tick <drill id> yes|no     # did that drill hit the right thing
    engine/.venv/bin/python scripts/checks/teach_report.py --call yes|no                # "she teaches better than looking it up myself"
    engine/.venv/bin/python scripts/checks/teach_report.py --wrong "what was wrong"     # a wrong key he found that he did not flag in chat

Run it each Sunday and paste the output into STATE.md. Ticks and his call live in `teach_ticks.json` next to the db.
Bars (step E, card E19): learning (after at least 80%, a week later at least 70%), trust (0 wrong keys, 0 spoken steps with no cite),
fit (at least 8 of 10 drills hit, a course kept three weeks, at most one nag a day), his call, spend inside the teach cap every day.
The wall, her acceptance exam (18 of 20) and tool-pick are not read here: run `scripts/checks/wall.sh` for those.
`--cites` also re-checks every cite against its source text (needs the live db copy; the plain run only checks that a cite is there).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DAY = 86400
SGT = 8 * 3600
BARS = {"lessons": 8, "drills": 10, "papers": 1, "after": 0.80, "week": 0.70, "hits": 8, "ticked": 10, "course_days": 21, "nags": 1}
NAG_KINDS = ("lesson", "drill", "class", "replan")          # `eve` is the one evening re-ask the card allows, counted apart
_EMPTY_TICKS = {"drills": {}, "his_call": None, "wrong_keys_told": []}


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t + SGT))


def _q(con, sql: str, args=()):
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:                         # a table that does not exist yet: nothing happened there
        return []


def ticks_path(db: str | Path) -> Path:
    return Path(db).parent / "teach_ticks.json"


def load_ticks(db: str | Path) -> dict:
    p = ticks_path(db)
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        d = {}
    out = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in _EMPTY_TICKS.items()}
    if isinstance(d, dict):
        out["drills"].update({str(k): bool(v) for k, v in (d.get("drills") or {}).items()})
        out["his_call"] = d.get("his_call") if d.get("his_call") in ("yes", "no") else None
        out["wrong_keys_told"] = [str(x) for x in (d.get("wrong_keys_told") or [])]
    return out


def save_ticks(db: str | Path, ticks: dict) -> None:
    ticks_path(db).write_text(json.dumps(ticks, indent=1, sort_keys=True))


# ---- the numbers -----------------------------------------------------------------------------------------------------
def _activity(con) -> dict:
    lessons = _q(con, "SELECT id, topic_id, started_at FROM teach_live WHERE status='done'")
    drills = _q(con, "SELECT d.id, d.topic_id, d.started_at, d.right, d.wrong, t.title FROM teach_drill d LEFT JOIN teach_topics t ON t.id=d.topic_id "
                     "WHERE d.kind='drill' AND d.status='done' ORDER BY d.started_at")
    papers = _q(con, "SELECT id, started_at FROM teach_paper_runs WHERE status IN ('done','stopped','timeout')")
    return {"lessons": len(lessons), "drills": drills, "papers": len(papers),
            "first": min([r[2] for r in lessons] + [r[2] for r in drills] + [r[1] for r in papers], default=None)}


def _plans(con) -> list:
    """The latest usable plan of each topic."""
    return _q(con, "SELECT p.topic_id, p.plan_json FROM teach_plans p WHERE p.status='usable' AND p.version="
                   "(SELECT MAX(version) FROM teach_plans q WHERE q.topic_id=p.topic_id AND q.status='usable')")


def _spoken_cites(plan: dict):
    """Every cite a plan asks for: each spoken explain, each check, guided, solo."""
    for st in plan.get("steps") or []:
        for e in st.get("explain") or []:
            yield "say", (e or {}).get("cite")
        yield "check", ((st or {}).get("check") or {}).get("cite")
    for k in ("guided", "solo"):
        if plan.get(k):
            yield k, (plan[k] or {}).get("cite")


def cites(con, checker=None) -> dict:
    """Spoken steps with no cite, and (with a checker) cites whose quote is not in the source. Both bars are 0."""
    total = missing = bad = 0
    for _tid, pj in _plans(con):
        try:
            plan = json.loads(pj)
        except ValueError:
            continue
        for _what, c in _spoken_cites(plan):
            total += 1
            if not (isinstance(c, dict) and c.get("source_id") and isinstance(c.get("chunk_no"), int) and str(c.get("quote") or "").strip()):
                missing += 1
            elif checker is not None and not checker(c["source_id"], c["chunk_no"], c["quote"]):
                bad += 1
    return {"total": total, "missing": missing, "bad": bad, "full_check": checker is not None}


def nags(con) -> dict:
    """What she asked him, per Singapore day, from the bite log (the gate has no log of its own). Dropped asks never reached him."""
    rows = _q(con, "SELECT at, kind, outcome FROM teach_bite WHERE outcome NOT IN ('dropped','none') AND kind!='match' AND outcome!=''")
    per: dict[str, int] = {}
    eve = 0
    for at, kind, _o in rows:
        if kind == "eve":
            eve += 1
        elif kind in NAG_KINDS:
            per[_day(at)] = per.get(_day(at), 0) + 1
    over = sorted(d for d, n in per.items() if n > BARS["nags"])
    return {"days": len(per), "max": max(per.values(), default=0), "over": over, "eve": eve}


def courses(con, now: float) -> dict:
    rows = _q(con, "SELECT id, goal, status, created_at FROM teach_courses WHERE status IN ('active','paused') ORDER BY created_at")
    best = max(rows, key=lambda r: now - r[3], default=None)
    if best is None:
        return {"count": 0, "span_days": 0, "goal": "", "status": ""}
    return {"count": len(rows), "span_days": int((now - best[3]) // DAY), "goal": best[1], "status": best[2]}


def spend(con, cap: float) -> dict:
    rows = _q(con, "SELECT ts, usd FROM spend WHERE purpose='teach'")
    per: dict[str, float] = {}
    for ts, usd in rows:
        per[_day(ts)] = per.get(_day(ts), 0.0) + usd
    over = sorted(d for d, u in per.items() if u > cap + 1e-9)
    return {"total": round(sum(per.values()), 4), "days": len(per), "max_day": round(max(per.values(), default=0.0), 4), "over": over, "cap": cap}


def trust(con, ticks: dict) -> dict:
    flagged = _q(con, "SELECT COUNT(*) FROM teach_slips WHERE flagged=1")
    n = (flagged[0][0] if flagged else 0)
    keylog = _q(con, "SELECT COUNT(*), COALESCE(SUM(ok),0) FROM teach_keylog")
    return {"flagged": n, "told": len(ticks["wrong_keys_told"]), "wrong": n + len(ticks["wrong_keys_told"]),
            "keys_checked": keylog[0][0] if keylog else 0, "keys_ok": keylog[0][1] if keylog else 0}


def build(con, *, now: float | None = None, cap: float = 0.10, ticks: dict | None = None, checker=None) -> dict:
    import learning_report
    now = time.time() if now is None else now
    ticks = ticks or {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in _EMPTY_TICKS.items()}
    con.row_factory = sqlite3.Row
    if not _q(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teach_topics'"):
        raise SystemExit("this database has no teaching tables yet (the installed engine is older than E1): install a build with step E first")
    act = _activity(con)
    learn = learning_report.build(con)
    drills = [{"id": d[0], "topic": d[5] or d[1], "right": d[3], "wrong": d[4], "tick": ticks["drills"].get(d[0])} for d in act["drills"]]
    ticked = [d for d in drills if d["tick"] is not None]
    hits = sum(1 for d in ticked if d["tick"])
    rep = {"since": act["first"], "days": int((now - act["first"]) // DAY) if act["first"] else 0,
           "lessons": act["lessons"], "drills": drills, "papers": act["papers"],
           "learning": learn, "trust": trust(con, ticks), "cites": cites(con, checker), "nags": nags(con),
           "course": courses(con, now), "spend": spend(con, cap), "hits": hits, "ticked": len(ticked), "his_call": ticks["his_call"]}
    rep["bars"] = bars(rep)
    return rep


def bars(r: dict) -> list[tuple[str, str, str]]:
    """(name, PASS|FAIL|WAITING, the plain reason). WAITING means the data is not there yet, not that it failed."""
    L, out = r["learning"], []

    def add(name, ok, why, waiting=False):
        out.append((name, "WAITING" if waiting else ("PASS" if ok else "FAIL"), why))
    add("used enough", r["lessons"] >= BARS["lessons"] and len(r["drills"]) >= BARS["drills"] and r["papers"] >= BARS["papers"],
        f"{r['lessons']} lessons (8), {len(r['drills'])} drills (10), {r['papers']} paper sessions (1)")
    add("learning: after", (L["after_avg"] or 0) >= BARS["after"], f"{_p(L['after_avg'])} over {L['counted']} topics (80%)", waiting=L["after_avg"] is None)
    add("learning: a week later", (L["week_avg"] or 0) >= BARS["week"], f"{_p(L['week_avg'])} over {L['week_n']} topics (70%)", waiting=L["week_avg"] is None)
    add("trust: wrong keys", r["trust"]["wrong"] == 0, f"{r['trust']['wrong']} found ({r['trust']['flagged']} flagged, {r['trust']['told']} told)")
    c = r["cites"]
    add("trust: every spoken step has a cite", c["missing"] == 0 and c["bad"] == 0,
        f"{c['missing']} with no cite, {c['bad']} not in the source" + ("" if c["full_check"] else " (shape only, run --cites for the text check)") + f", of {c['total']}",
        waiting=c["total"] == 0)
    add("fit: drills hit the right thing", r["hits"] >= BARS["hits"] and r["ticked"] >= BARS["ticked"], f"{r['hits']} of {r['ticked']} ticked (8 of 10)",
        waiting=r["ticked"] < BARS["ticked"] and (r["ticked"] - r["hits"]) <= BARS["ticked"] - BARS["hits"])
    add("fit: a course kept three weeks", r["course"]["span_days"] >= BARS["course_days"], f"{r['course']['span_days']} days (21)", waiting=r["course"]["count"] == 0)
    n = r["nags"]
    add("fit: at most one nag a day", not n["over"], f"most in a day {n['max']}, days over: {', '.join(n['over']) or 'none'}, evening re-asks {n['eve']}", waiting=n["days"] == 0)
    add("his call", r["his_call"] == "yes", {"yes": "yes", "no": "no", None: "not said yet"}[r["his_call"]], waiting=r["his_call"] is None)
    s = r["spend"]
    add("spend inside the teach cap", not s["over"], f"${s['total']:.3f} over {s['days']} days, most in a day ${s['max_day']:.3f}, cap ${s['cap']:.2f}, over: {', '.join(s['over']) or 'none'}")
    return out


def _p(x) -> str:
    return "n/a" if x is None else f"{round(x * 100)}%"


def render(r: dict) -> str:
    import learning_report
    out = [f"Step E, three weeks of being taught ({r['days']} days in)", ""]
    out += [f"  {'PASS' if s == 'PASS' else s:7} {name}: {why}" for name, s, why in r["bars"]]
    out += ["", learning_report.render(r["learning"]), "", "Drills to tick (did it hit the right thing?)"]
    out += [f"  {d['id']}  {d['topic']}  {d['right']} right, {d['wrong']} wrong  [{'hit' if d['tick'] else 'miss' if d['tick'] is False else 'not ticked'}]" for d in r["drills"]] or ["  none yet"]
    out += ["", "Not read here: wall.sh (all PASS), her acceptance exam (18 of 20), tool-pick at or above baseline."]
    return "\n".join(out)


def _cite_checker():
    """A real text check of every cite, on a copy of the live db (the harness never writes the live one)."""
    import _harness
    from misty_engine.teach import sources
    ctx, home = _harness.temp_ctx(copy_live=True)
    return (lambda sid, no, quote: sources.find_quote(ctx, sid, no, quote)), (ctx, home)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "Misty" / "misty.db"))
    ap.add_argument("--cap", type=float, default=0.10)
    ap.add_argument("--cites", action="store_true")
    ap.add_argument("--tick", nargs=2, metavar=("DRILL_ID", "yes|no"))
    ap.add_argument("--call", choices=("yes", "no"))
    ap.add_argument("--wrong", metavar="TEXT")
    a = ap.parse_args()
    if not Path(a.db).exists():
        print(f"no database at {a.db}", file=sys.stderr)
        return 1
    ticks = load_ticks(a.db)
    if a.tick or a.call or a.wrong:
        if a.tick:
            if a.tick[1] not in ("yes", "no"):
                print("say yes or no", file=sys.stderr)
                return 2
            ticks["drills"][a.tick[0]] = a.tick[1] == "yes"
        if a.call:
            ticks["his_call"] = a.call
        if a.wrong:
            ticks["wrong_keys_told"].append(a.wrong)
        save_ticks(a.db, ticks)
    checker, held = (None, None)
    if a.cites:
        checker, held = _cite_checker()
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    print(render(build(con, cap=a.cap, ticks=ticks, checker=checker)))
    if held:
        import _harness
        _harness.cleanup(held[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
