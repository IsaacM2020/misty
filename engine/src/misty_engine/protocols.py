"""Protocols (step D, D6): a named list of moves he made by talking ("when I say let's do Math, open my Math folder and start a
45 minute timer"). Saved only from chat. Every step is checked against the move registry before it is kept: the move exists,
its params pass its schema, no step is `never`. A step may also be `open_fact {key}`, so "my Math folder" stays a fact he can
change later. An `ask` step inside a protocol still asks, every time. Running them is D7."""
from __future__ import annotations

import json
import re
import secrets

from . import facts, moves
from .errors import Fatal
from .facts import norm_key
from .logs import log_event
from .scrub import find_secrets

MAX_STEPS, MAX_TRIGGERS, TRIGGER_MAX = 12, 6, 60


def norm_trigger(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(t).lower().replace("'", "").replace("\u2019", "")).split())


def active(con) -> list:
    return con.execute("SELECT * FROM protocols WHERE status='active' ORDER BY updated_at DESC").fetchall()


def get(con, key: str):
    return con.execute("SELECT * FROM protocols WHERE status='active' AND key=?", (norm_key(key),)).fetchone()


def triggers_of(r) -> list[str]:
    return json.loads(r["triggers"])


def steps_of(r) -> list[dict]:
    return json.loads(r["steps"])


def end_steps_of(r) -> list[dict]:
    return json.loads(r["end_steps"])


def check_steps(ctx, steps, label: str = "steps") -> tuple[list[dict] | None, str]:
    """(clean steps, "") or (None, the reason, in words the model can say back)."""
    if not isinstance(steps, list) or not steps:
        return None, f"{label} must be a list with at least one step"
    if len(steps) > MAX_STEPS:
        return None, f"at most {MAX_STEPS} {label}"
    out = []
    for i, st in enumerate(steps, 1):
        if not isinstance(st, dict):
            return None, f"{label} {i} must be an object with move and params"
        name = str(st.get("move") or "")
        params = st.get("params") or {}
        if name == "open_fact":
            key = str(params.get("key") or "") if isinstance(params, dict) else ""
            if not key or facts.same_key(ctx, key) is None:
                return None, f"{label} {i}: he has no saved fact called \"{key}\" to open"
            out.append({"move": "open_fact", "params": {"key": facts.same_key(ctx, key)}})
            continue
        m = moves.MOVES.get(name)
        if m is None:
            return None, f"{label} {i}: no move called {name or '(none)'}. The moves are: {', '.join(moves.MOVES)}, open_fact"
        if m.risk == "never":
            return None, f"{label} {i}: {name} is something she never does, so it can't be in a protocol"
        clean, err = moves.registry.check_params(m, params)
        if clean is None:
            return None, f"{label} {i} ({name}): {err}"
        out.append({"move": name, "params": clean})
    return out, ""


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_SETS = {"daily": DAYS, "weekdays": DAYS[:5], "weekends": DAYS[5:]}
WHEN_KINDS = ("time", "first_open", "class_end", "timer_end")


def check_when(when, steps, key: str) -> tuple[dict | None, str]:
    """A routine's trigger: {kind: time, at: "07:30", days}, {kind: first_open}, {kind: class_end, subject?}, {kind: timer_end, label}."""
    if not isinstance(when, dict) or when.get("kind") not in WHEN_KINDS:
        return None, f"when.kind must be one of: {', '.join(WHEN_KINDS)}"
    kind = when["kind"]
    if kind == "time":
        m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(when.get("at") or "").strip())
        if not m:
            return None, "when.at must be a time like 07:30"
        raw = when.get("days") or "daily"
        days = DAY_SETS.get(str(raw).lower()) if isinstance(raw, str) else tuple(str(d).lower()[:3] for d in raw)
        if not days or any(d not in DAYS for d in days):
            return None, "when.days must be daily, weekdays, weekends, or a list like [mon, wed]"
        return {"kind": "time", "at": f"{int(m[1]):02d}:{m[2]}", "days": list(days)}, ""
    if kind == "first_open":
        return {"kind": "first_open"}, ""
    if kind == "class_end":
        return {"kind": "class_end", "subject": " ".join(str(when.get("subject") or "").split())[:40]}, ""
    label = " ".join(str(when.get("label") or "").split())[:60].lower()
    if not label:
        return None, "when.label is the timer's label (the timer that ends starts this)"
    if any(s["move"] == "timer_start" and str(s["params"].get("label") or "").lower() == label for s in steps):
        return None, "a routine that starts a timer with the same label it waits for would run forever"
    return {"kind": "timer_end", "label": label}, ""


def all_safe(steps: list[dict]) -> bool:
    return all(s["move"] == "open_fact" or (s["move"] in moves.MOVES and moves.MOVES[s["move"]].risk == "safe") for s in steps)


def save(ctx, key: str, triggers, steps, end_steps=None, when=None, auto=None) -> dict:
    """Add a protocol, or replace the one with the same key. Returns {action, key, triggers, steps, end_steps, when, auto} or raises Fatal(reason).
    `when` makes it a routine (D9). `auto` (run without asking) is only allowed when every step is safe."""
    k = norm_key(key)
    if not k:
        raise Fatal("a protocol needs a short name")
    triggers = triggers if triggers is not None else []
    was = get(ctx.con, k)
    if not isinstance(triggers, list) or len(triggers) > MAX_TRIGGERS or (not triggers and not when and not (was is not None and was["when_spec"])):
        raise Fatal(f"triggers must be 1 to {MAX_TRIGGERS} phrases he would say (or give a when)")
    trig = []
    for t in triggers:
        n = norm_trigger(t)
        if not n or len(n) > TRIGGER_MAX:
            raise Fatal(f"a trigger must be a short phrase (at most {TRIGGER_MAX} characters)")
        if n not in trig:
            trig.append(n)
    st, err = check_steps(ctx, steps)
    if st is None:
        raise Fatal(err)
    es: list[dict] = []
    if end_steps:
        es, err = check_steps(ctx, end_steps, "end steps")
        if es is None:
            raise Fatal(err)
    wh, auto_off = None, False
    if when is not None:
        wh, err = check_when(when, st, k)
        if wh is None:
            raise Fatal(err)
    elif was is not None and was["when_spec"]:                # changing the steps keeps its trigger
        wh = json.loads(was["when_spec"])
    explicit = auto is not None
    if not explicit:
        auto = bool(was["auto"]) if was is not None else False
    auto = bool(auto)
    if auto and wh is None:
        raise Fatal("only a routine (one with a when) can run on its own")
    if auto and not all_safe(st):
        if explicit:
            raise Fatal("a routine can only run without asking when every step is safe: one of these steps asks first, so it can't skip that")
        auto, auto_off = False, True                          # an earlier "just do it" does not cover a new step that asks
    if find_secrets(json.dumps([k, trig, st, es, wh]), "file"):
        log_event("PROTOCOL_REFUSED_SECRET", n=1)
        raise Fatal("that looks like a password or key, so it is not saved")
    for other in active(ctx.con):                         # one phrase must mean one protocol
        if other["key"] != k:
            clash = set(triggers_of(other)) & set(trig)
            if clash:
                raise Fatal(f"\"{sorted(clash)[0]}\" already starts his {other['key']} protocol")
    now = ctx.clock.now()
    pid = "pr_" + secrets.token_hex(5)
    with ctx.con:
        if was is not None:
            ctx.con.execute("UPDATE protocols SET status='replaced', replaced_by=?, updated_at=? WHERE id=?", (pid, now, was["id"]))
        ctx.con.execute("INSERT INTO protocols(id, key, triggers, steps, end_steps, created_at, updated_at, runs, last_run_at, when_spec, auto) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, k, json.dumps(trig), json.dumps(st), json.dumps(es), now, now,
                         was["runs"] if was is not None else 0, was["last_run_at"] if was is not None else None,
                         json.dumps(wh) if wh else "", int(auto)))
    log_event("PROTOCOL_SAVED", code="updated" if was is not None else "added")
    return {"action": "updated" if was is not None else "added", "key": k, "triggers": trig, "steps": st, "end_steps": es,
            "when": wh, "auto": auto, **({"auto_off": "it now has a step that asks, so it will ask before running"} if auto_off else {})}


def drop(ctx, key: str) -> bool:
    r = get(ctx.con, key)
    if r is None:
        return False
    with ctx.con:
        ctx.con.execute("UPDATE protocols SET status='dropped', updated_at=? WHERE id=?", (ctx.clock.now(), r["id"]))
    log_event("PROTOCOL_DROPPED")
    return True


def say_step(st: dict) -> str:
    """One step in plain words, for reading back to him."""
    p = st.get("params") or {}
    inner = ", ".join(f"{k} {v}" for k, v in p.items())
    return st["move"].replace("_", " ") + (f" ({inner})" if inner else "")


def when_of(r) -> dict | None:
    return json.loads(r["when_spec"]) if r["when_spec"] else None


def say_when(w: dict | None) -> str:
    if not w:
        return ""
    k = w["kind"]
    if k == "time":
        d = w.get("days") or []
        return f"at {w['at']} " + ("every day" if len(d) == 7 else "on weekdays" if d == list(DAYS[:5]) else "on " + ", ".join(d))
    if k == "first_open":
        return "the first time he is at the Mac each morning"
    if k == "class_end":
        return "when " + (f"{w['subject']} class" if w.get("subject") else "a class") + " ends"
    return f"when his {w['label']} timer ends"


def describe(r) -> dict:
    w = when_of(r)
    return {"key": r["key"], "triggers": triggers_of(r), "steps": [say_step(s) for s in steps_of(r)],
            "end_steps": [say_step(s) for s in end_steps_of(r)], "runs": r["runs"],
            **({"when": say_when(w), "auto": bool(r["auto"])} if w else {})}


def block(con) -> str:
    """For the prompt: name and triggers only. Empty when he has none."""
    rows = active(con)
    if not rows:
        return ""
    def one(r):
        s = f"- {r['key']}: " + (", ".join(f"\"{t}\"" for t in triggers_of(r)) or "(no spoken trigger)")
        w = when_of(r)
        return s + (f" [routine, {say_when(w)}{', runs without asking' if r['auto'] else ', asks first'}]" if w else "")
    return ("His protocols (saying a trigger runs it; you don't run them yourself):\n" + "\n".join(one(r) for r in rows[:30]))


# ---- D7: matching what he said, and running it

QUESTION = re.compile(r"^(what|whats|what's|where|which|when|who|why|how|is|are|does|do|did|can|could|would|should|will)\b")
NEGATION = {"not", "no", "never", "dont", "don't", "cancel", "without", "stop", "nothing", "isnt", "isn't", "wont", "won't"}
SHORT_WORDS = 8              # a trigger inside a message needs the message under this many words
EXTRA_WORDS = 3              # ...and at most this many words that are not the trigger ("ok", "please", "hey misty")
EMBED_MAX_WORDS = 6
EMBED_THRESHOLD = 0.97       # from the match set (protocol_match.py --calibrate): near-misses reach 0.948, so only a near-copy passes


def _words(text: str) -> list[str]:
    return norm_trigger(text).split()


def _contains(words: list[str], trig: list[str]) -> bool:
    n = len(trig)
    return n > 0 and any(words[i:i + n] == trig for i in range(len(words) - n + 1))


def match(ctx, text: str):
    """The protocol this message starts, or None. Exact trigger first, then the trigger inside a short message, then (short
    messages only) a close embedding. Never a long message, a question, or a negation."""
    rows = active(ctx.con)
    if not rows:
        return None
    w = _words(text)
    if not w:
        return None
    whole = " ".join(w)
    offer = take_offer(ctx, whole)                            # "yes" to a routine she just offered
    if offer is not None:
        return offer
    for r in rows:
        if whole in triggers_of(r):
            return r
    if len(w) >= SHORT_WORDS or QUESTION.match(whole) or text.strip().endswith("?") or NEGATION & set(w):
        return None
    hits = []
    for r in rows:
        for t in triggers_of(r):
            tw = t.split()
            if _contains(w, tw) and len(w) - len(tw) <= EXTRA_WORDS:
                hits.append((len(tw), r))
    if hits:
        hits.sort(key=lambda h: -h[0])
        return hits[0][1] if len({h[1]["id"] for h in hits}) == 1 or hits[0][0] > hits[1][0] else None
    if len(w) <= EMBED_MAX_WORDS and getattr(ctx, "embedder", None) is not None:
        q = facts._embed(ctx, whole)
        best, best_sim = None, EMBED_THRESHOLD
        for r in rows:
            for t in triggers_of(r):
                v = facts._embed(ctx, t)
                s = float(q @ v) if q is not None and v is not None and q.shape == v.shape else 0.0
                if s >= best_sim:
                    best, best_sim = r, s
        return best
    return None


END_WORDS = {"im done", "i'm done", "i am done", "all done", "done", "that's all for now", "thats all for now"}


def match_end(ctx, text: str):
    """"I'm done" or "stop math mode": the protocol whose end steps should run (the last one that ran today with end steps)."""
    from .clock import sgt_date
    whole = " ".join(_words(text))
    rows = [r for r in active(ctx.con) if end_steps_of(r)]
    if not rows:
        return None
    for r in rows:
        k = r["key"]
        if whole in (f"stop {k}", f"end {k}", f"finish {k}", f"stop my {k}", f"stop {k} protocol", f"{k} off", f"{k} is over"):
            return r
    if whole in {norm_trigger(x) for x in END_WORDS}:
        today = sgt_date(ctx.clock.now())
        ran = [r for r in rows if r["last_run_at"] and sgt_date(r["last_run_at"]) == today]
        ran.sort(key=lambda r: -r["last_run_at"])
        return ran[0] if ran else None
    return None


async def run(ctx, p, confirm, on_status, *, steps_key: str = "steps", run_id: str | None = None) -> dict:
    """Run a protocol's steps in order. Each step is one call through the move registry with its own call_id (run id + step
    number, so a crash and a retry never does a step twice). Stops at the first step that fails or is refused. An `ask` step
    asks, every time. Returns {ok, protocol, run_id, done: [...], failed: {step, move, why} | None, unproved: [...]}."""
    import time
    t0 = time.monotonic()
    steps = end_steps_of(p) if steps_key == "end_steps" else steps_of(p)
    run_id = run_id or "run" + secrets.token_hex(4)
    done: list[str] = []
    unproved: list[str] = []
    failed = None
    with ctx.con:
        ctx.con.execute("UPDATE protocols SET runs=runs+1, last_run_at=? WHERE id=?", (ctx.clock.now(), p["id"]))
    for n, st in enumerate(steps, 1):
        if n == 1:
            log_event("PROTOCOL_FIRST_STEP", ms=int((time.monotonic() - t0) * 1000))
        await on_status(f"{p['key']}: {say_step(st)}…")
        cid = f"{run_id}-{n}"
        if st["move"] == "open_fact":
            from . import chat
            res = await chat.run_tool(ctx, "open_fact", st["params"], on_status=on_status, confirm=confirm)
        else:
            res = await moves.run(ctx, st["move"], st["params"], cid, confirm, "him", protocol=run_id)
        if not res.get("ok"):
            failed = {"step": n, "move": st["move"], "why": str(res.get("error") or res.get("what") or "it did not work")[:200]}
            if res.get("declined"):
                failed["declined"] = True
            break
        done.append(say_step(st))
        if st["move"] != "open_fact" and not res.get("verified"):
            unproved.append(say_step(st))
    log_event("PROTOCOL_RUN", code="ok" if failed is None else "failed", n=len(done))
    return {"ok": failed is None, "protocol": p["key"], "run_id": run_id, "done": done, "failed": failed, "unproved": unproved,
            **({"note": "the steps after the failed one were not run"} if failed else {})}


# ---- D9: routines. A protocol with a `when` starts from a time or an event. It goes through the gate like everything she
# says on her own: the gate's hard rules (class, calls, quiet hours, a conversation) hold. By default she asks ("Class is over.
# Run your after-class routine?") and he says yes. He can make one automatic ("just do it") only when every step is safe.

ROUTINE_STALE_S = 3600        # a routine that waited this long is dropped, never run late
YES_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "do it", "go ahead", "yes please", "please do", "run it", "go"}
OFFER = "routine_offer"


def take_offer(ctx, whole: str):
    """"yes" right after she offered a routine: that routine's protocol (once)."""
    o = (ctx.extras or {}).get(OFFER)
    if not o or whole not in YES_WORDS:
        return None
    ctx.extras.pop(OFFER, None)
    if ctx.clock.now() - o["at"] > ROUTINE_STALE_S:
        return None
    return ctx.con.execute("SELECT * FROM protocols WHERE id=? AND status='active'", (o["id"],)).fetchone()


def _fired(ctx, key: str) -> bool:
    return ctx.con.execute("SELECT 1 FROM routine_fires WHERE key=?", (key,)).fetchone() is not None


def _record(ctx, key: str, pid: str, outcome: str) -> None:
    with ctx.con:
        ctx.con.execute("INSERT OR IGNORE INTO routine_fires(key, protocol, at, outcome) VALUES (?,?,?,?)", (key, pid, ctx.clock.now(), outcome))


def candidates(ctx, now: float) -> list[tuple[str, object, float, str]]:
    """(unique key of this trigger, the protocol row, when it happened, a plain sentence about it) for every routine that is
    triggered now and not yet dealt with. Pure reading: nothing is changed."""
    from datetime import datetime
    from .clock import SGT, sgt_date
    out = []
    dt = datetime.fromtimestamp(now, SGT)
    today = sgt_date(now)
    for r in active(ctx.con):
        w = when_of(r)
        if not w:
            continue
        k, kind = r["key"], w["kind"]
        if kind == "time":
            if DAYS[dt.weekday()] in w["days"]:
                h, m = (int(x) for x in w["at"].split(":"))
                at = dt.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
                if at <= now:
                    out.append((f"time:{k}:{today}", r, at, f"It's {w['at']}."))
        elif kind == "first_open":
            from . import brief
            if brief.FROM_HOUR <= dt.hour < brief.UNTIL_HOUR and brief._at_the_mac(ctx, now):
                if ctx.extras is None:
                    ctx.extras = {}
                seen = ctx.extras.setdefault("routine_seen", {})
                out.append((f"first_open:{k}:{today}", r, seen.setdefault(f"{k}:{today}", now), "Good to see you at the Mac."))
        elif kind == "class_end":
            q = "SELECT id, subject, ended_at FROM class_sessions WHERE status='ended' AND ended_at>=?"
            for s in ctx.con.execute(q, (now - 2 * ROUTINE_STALE_S,)):
                if not w.get("subject") or w["subject"].lower() in (s["subject"] or "").lower():
                    out.append((f"class_end:{k}:{s['id']}", r, s["ended_at"], "Class is over."))
        elif kind == "timer_end":
            for t in ctx.con.execute("SELECT id, label, closed_at FROM timers WHERE status='done' AND closed_at>=?", (now - 2 * ROUTINE_STALE_S,)):
                if t["label"].lower() == w["label"]:
                    out.append((f"timer_end:{k}:{t['id']}", r, t["closed_at"], f"Your {t['label']} timer is up."))
    return out


async def fire_due(ctx) -> int:
    """Called every minute. For each routine whose trigger just happened: drop it if it waited over an hour, wait if the gate
    says not now, otherwise tell him (and run it when he allowed that). Each trigger is dealt with once."""
    from . import gate
    if ctx.extras is None:
        ctx.extras = {}
    now = ctx.clock.now()
    n = 0
    async def quiet(_s): ...
    for key, r, at, why in candidates(ctx, now):
        if _fired(ctx, key):
            continue
        if now - at > ROUTINE_STALE_S:
            _record(ctx, key, r["key"], "dropped")
            log_event("ROUTINE", code="dropped")
            continue
        auto = bool(r["auto"]) and all_safe(steps_of(r))          # re-checked: a changed step never rides an old "just do it"
        text = f"{why} Running your {r['key']} routine." if auto else f"{why} Run your {r['key']} routine?"
        m = gate.Moment(text, "soon", topic="routine", topic_text=f"your {r['key']} routine", key=f"routine:{key}", plain=True)
        d = gate._decide(ctx, m, now)
        if d.action in ("wait", "drop"):
            if d.action == "drop":
                _record(ctx, key, r["key"], "dropped")
            continue                                              # not now: try again next minute, until it is stale
        await gate.submit(ctx, m)
        n += 1
        if auto:
            _record(ctx, key, r["key"], "ran")
            res = await run(ctx, r, None, quiet)
            log_event("ROUTINE", code="ran" if res["ok"] else "failed")
            if not res["ok"]:
                f = res["failed"]
                await gate.submit(ctx, gate.Moment(f"Your {r['key']} routine stopped at step {f['step']}: {f['why']}", "soon", topic="routine",
                                                   topic_text=f"your {r['key']} routine", key=f"routine-failed:{key}", plain=True))
        else:
            ctx.extras[OFFER] = {"id": r["id"], "at": now}
            _record(ctx, key, r["key"], "told")
            log_event("ROUTINE", code="told")
    return n
