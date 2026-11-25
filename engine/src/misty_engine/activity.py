"""Step C7: the activity trail. The app tells the engine when he switches app or window (`activity.note`); one row is kept per
stretch in one app and document. A stretch under 20 seconds is not kept. Private apps are never kept. Rows older than 30 days
go (the one place deletion is allowed: it is her own log, and the retention is the privacy promise). He can pause it by talking
(setting activity_trail). Only app, window title and doc link are stored, never screen text."""
from __future__ import annotations

from datetime import datetime, timedelta

from .clock import SGT, sgt_date
from .scrub import scrub_text

MIN_SECONDS = 20
KEEP_DAYS = 30
IDLE = 1800          # no word from the app for this long: the stretch ended when the last word came
GAP = 120            # the same thing resumed within this many seconds is one stretch


def _clean(v, cap: int) -> str:
    return "/".join(scrub_text(p).text for p in str(v or "").strip()[:cap].split("/"))


def on(ctx) -> bool:
    try:
        return bool(ctx.settings.get("activity_trail"))
    except KeyError:
        return True


def note(ctx, bundle: str, app: str, title: str = "", doc: str = "") -> dict:
    """He is now in this app and document. Closes the stretch before it and opens a new one. Returns {kept: bool}."""
    from . import privacy
    now = ctx.clock.now()
    con = ctx.con
    bundle = str(bundle or "")[:120]
    if not on(ctx) or not bundle or bundle in privacy.bundles(ctx):
        _close(ctx, now)
        return {"kept": False}
    title, doc, app = _clean(title, 200), _clean(doc, 300), _clean(app, 80)
    last = con.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 1").fetchone()
    if last is not None and last["bundle"] == bundle and last["title"] == title and last["doc"] == doc and now - last["until"] < GAP:
        with con:
            con.execute("UPDATE activity SET until=? WHERE id=?", (now, last["id"]))
        return {"kept": True, "merged": True}
    _close(ctx, now)
    with con:
        con.execute("INSERT INTO activity(at, until, bundle, app, title, doc) VALUES (?,?,?,?,?,?)", (now, now, bundle, app, title, doc))
    _prune_daily(ctx, now)
    return {"kept": True}


def _close(ctx, now: float) -> None:
    """End the open stretch at `now`; one under MIN_SECONDS is dropped (it was a pass-through, not work)."""
    con = ctx.con
    last = con.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 1").fetchone()
    if last is None or last["until"] < last["at"]:
        return
    end = now if now - last["until"] <= IDLE else last["until"] + GAP      # silence over 30 min was sleep or idle, not work
    with con:
        if end - last["at"] < MIN_SECONDS:
            con.execute("DELETE FROM activity WHERE id=?", (last["id"],))
        else:
            con.execute("UPDATE activity SET until=? WHERE id=?", (end, last["id"]))


def prune(ctx, now: float | None = None) -> int:
    cutoff = (now if now is not None else ctx.clock.now()) - KEEP_DAYS * 86400
    with ctx.con:
        return ctx.con.execute("DELETE FROM activity WHERE until < ?", (cutoff,)).rowcount


_pruned: dict[int, str] = {}


def _prune_daily(ctx, now: float) -> None:
    day = sgt_date(now)
    if _pruned.get(id(ctx.con)) != day:
        _pruned[id(ctx.con)] = day
        prune(ctx, now)


def _bounds(ctx, day: str, frm: str, to: str) -> tuple[float, float]:
    d = datetime.fromisoformat(day) if day else datetime.fromtimestamp(ctx.clock.now(), SGT)
    base = d.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=SGT)

    def at(hhmm: str, default: timedelta) -> datetime:
        try:
            h, m = (hhmm.split(":") + ["0"])[:2]
            return base + timedelta(hours=int(h), minutes=int(m))
        except (ValueError, AttributeError):
            return base + default
    return at(frm, timedelta()).timestamp(), at(to, timedelta(days=1)).timestamp()


def stretches(ctx, day: str = "", frm: str = "", to: str = "") -> dict:
    """What he was working on, grouped by document: minutes each, most first."""
    if not on(ctx):
        return {"ok": True, "trail_off": True, "note": "the activity trail is paused, so there is nothing to tell"}
    lo, hi = _bounds(ctx, day, frm, to)
    rows = ctx.con.execute("SELECT * FROM activity WHERE until > ? AND at < ? ORDER BY at", (lo, hi)).fetchall()
    groups: dict[tuple, dict] = {}
    for r in rows:
        secs = min(r["until"], hi) - max(r["at"], lo)
        if secs < MIN_SECONDS:
            continue
        key = (r["bundle"], r["doc"] or r["title"])
        g = groups.setdefault(key, {"app": r["app"], "title": r["title"], "doc": r["doc"], "seconds": 0.0})
        g["seconds"] += secs
        g["title"] = g["title"] or r["title"]
    out = sorted(groups.values(), key=lambda g: -g["seconds"])[:12]
    return {"ok": True, "from": datetime.fromtimestamp(lo, SGT).strftime("%a %H:%M"), "to": datetime.fromtimestamp(hi, SGT).strftime("%a %H:%M"),
            "stretches": [{"app": g["app"], "title": g["title"][:120], "doc": g["doc"][:200], "minutes": round(g["seconds"] / 60)} for g in out]}


def lately(ctx, hours: int = 24, n: int = 5) -> list[str]:
    """For work_on and the brief: a short line per document he spent real time on lately."""
    if not on(ctx):
        return []
    now = ctx.clock.now()
    rows = ctx.con.execute("SELECT app, title, doc, SUM(until-at) AS s FROM activity WHERE until > ? GROUP BY bundle, COALESCE(NULLIF(doc,''), title) "
                           "ORDER BY s DESC LIMIT ?", (now - hours * 3600, n)).fetchall()
    return [f"{x['title'] or x['app']} in {x['app']} ({round(x['s'] / 60)} min)" for x in rows if x["s"] >= 120]


def current_stretch(ctx, now: float | None = None) -> dict | None:
    """The newest kept stretch if it is still going (the app spoke within GAP seconds): {bundle, app, title, doc, seconds}.
    None when the trail is paused, nothing is kept (a private app closes the stretch) or it went quiet. For the Now block."""
    if not on(ctx):
        return None
    now = ctx.clock.now() if now is None else now
    r = ctx.con.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 1").fetchone()
    if r is None or r["until"] < r["at"] or now - r["until"] >= GAP:
        return None
    return {"bundle": r["bundle"], "app": r["app"], "title": r["title"], "doc": r["doc"], "seconds": max(0.0, now - r["at"])}
