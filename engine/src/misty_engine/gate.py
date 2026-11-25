"""The gate (Season 3, Day 5): when she may speak up on her own. Plain code, not the model.

Something worth telling him arrives as a Moment (what, how urgent, where it came from). The gate answers with one of:
  say   speak it now (and put it in the chat)
  card  show it silently (he is typing and it can't wait)
  ask   ask first ("I have an update on X. Want it?") because the last one on this topic was dismissed
  wait  keep it; try again at the next break
  drop  never say it (already told, or too old)
The rules run in a fixed order and the first one that applies wins, so a decision always has one plain reason.
Nothing here knows about cricket, school or any one person."""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from . import ways
from .clock import SGT, sgt_day_start
from .logs import log_event

Urgency = Literal["now", "soon", "later"]
URGENCIES = ("now", "soon", "later")
TTL = {"now": 2 * 3600, "soon": 12 * 3600, "later": 36 * 3600}      # after this long a held item is stale news
TYPING_IDLE_S = 10          # input this recent means he is typing or working: nothing is spoken
BREAK_IDLE_S = 25           # a pause this long is a break: "soon" things may be said
BIG_BREAK_S = 120           # "later" things wait for a real pause (or the morning brief)
PRESENCE_STALE_S = 30       # no word from the app for this long: she can't see him, so she waits
MIN_GAP_S = 120             # never two in a row closer than this
BACKOFF_AFTER = 2           # this many dismissals in a row on a topic...
BACKOFF_S = 24 * 3600       # ...and that topic is quiet for a day
DISMISSED = ("down", "ignored", "declined", "dismissed")
CAP_MAX = 20
ASK_WINDOW_S = 2 * 3600     # an unanswered ask is forgotten after this
OPTIONS = {"daily_limit": "cap", "quiet_from": "quiet_from", "quiet_to": "quiet_to"}


@dataclass(frozen=True)
class Moment:
    text: str
    urgency: str = "soon"
    topic: str = "general"           # which follow or source it came from (back-off is per topic)
    topic_text: str = ""             # the topic in his words, for an ask
    source_name: str | None = None
    url: str | None = None
    key: str = ""                    # what it is about; the same key is never told twice
    plain: bool = False              # her own routine (the morning brief): lessons about news do not apply
    own: bool = False                # something he set up himself and is waiting for (a timer): may be said while he types, skips the daily limit and the gap
    quiet: bool = False              # show, never speak: a "say" becomes a card (class start: her voice would land in the recording)

    def keyed(self) -> str:
        return self.key or f"{self.topic}:{_norm(self.text)[:60]}"


@dataclass(frozen=True)
class Presence:
    idle_s: float | None             # seconds since he last touched keyboard or mouse; None = the app can't tell
    voice: str = "idle"              # idle | listening | thinking | speaking (a conversation with her)
    at: float = 0.0                  # when the app said so


@dataclass(frozen=True)
class Decision:
    action: str                      # say | card | ask | wait | drop
    why: str


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


def is_quiet(hour: int, quiet_from: int, quiet_to: int) -> bool:
    if quiet_from == quiet_to:
        return False
    if quiet_from < quiet_to:
        return quiet_from <= hour < quiet_to
    return hour >= quiet_from or hour < quiet_to          # wraps midnight, e.g. 23 to 7


def decide(m: Moment, *, now: float, presence: Presence | None, in_session: bool, told_today: int, last_told_at: float | None,
           cap: int, quiet_from: int, quiet_to: int, topic_feedback: list[tuple[str, float]], already: bool,
           age_s: float = 0.0) -> Decision:
    """The whole gate, as a function of plain facts. topic_feedback: this topic's answered items, newest first."""
    u = m.urgency if m.urgency in URGENCIES else "soon"
    if already:
        return Decision("drop", "already told")
    if age_s > TTL[u]:
        return Decision("drop", "too old to matter")
    if in_session:
        return Decision("wait", "in class or on a call")
    if is_quiet(datetime.fromtimestamp(now, SGT).hour, quiet_from, quiet_to):
        return Decision("wait", "quiet hours")
    own = m.own and u == "now"           # D2: his own timer. Class, calls, quiet hours and a conversation still stop it.
    if told_today >= cap and not own:
        return Decision("wait", "daily limit reached")
    recent = topic_feedback[:BACKOFF_AFTER]
    if not own and len(recent) == BACKOFF_AFTER and all(f in DISMISSED for f, _ in recent) and now - recent[0][1] < BACKOFF_S:
        return Decision("wait", "backing off this topic")
    if not own and last_told_at is not None and now - last_told_at < MIN_GAP_S:
        return Decision("wait", "just told him something")
    if presence is None or presence.idle_s is None or now - presence.at > PRESENCE_STALE_S:
        return Decision("wait", "can't tell if he is busy")
    if presence.voice != "idle":
        return Decision("wait", "he is talking with her")
    if presence.idle_s < TYPING_IDLE_S:
        if own:
            return Decision("say", "a timer he set himself")
        return Decision("card", "he is working, silent card") if u == "now" else Decision("wait", "he is working")
    need = {"now": TYPING_IDLE_S, "soon": BREAK_IDLE_S, "later": BIG_BREAK_S}[u]
    if presence.idle_s < need:
        return Decision("wait", "not a long enough break")
    if u != "now" and topic_feedback and topic_feedback[0][0] in DISMISSED:
        return Decision("ask", "the last one on this topic was dismissed")
    return Decision("say", "a good moment")


# ---- state read from the database

def _opts(ctx) -> dict:
    d = dict(ctx.settings.get("speak_up"))
    return {"cap": int(d.get("cap", 5)), "quiet_from": int(d.get("quiet_from", 23)), "quiet_to": int(d.get("quiet_to", 7))}


def set_presence(ctx, idle_s: float | None, voice: str = "idle") -> None:
    if ctx.extras is None:
        ctx.extras = {}
    ctx.extras["presence"] = Presence(None if idle_s is None else max(0.0, float(idle_s)),
                                      voice if voice in ("idle", "listening", "thinking", "speaking") else "idle",
                                      ctx.clock.now())


def presence(ctx) -> Presence | None:
    return (ctx.extras or {}).get("presence")


def in_session(con) -> bool:
    return con.execute("SELECT 1 FROM class_sessions WHERE status IN ('live','paused') LIMIT 1").fetchone() is not None


def told_today(con, now: float) -> int:
    return con.execute("SELECT COUNT(*) FROM told WHERE at >= ?", (sgt_day_start(now),)).fetchone()[0]


def _last_told_at(con) -> float | None:
    r = con.execute("SELECT MAX(at) FROM told").fetchone()
    return r[0] if r and r[0] is not None else None


def _topic_feedback(con, topic: str) -> list[tuple[str, float]]:
    rows = con.execute("SELECT feedback, COALESCE(feedback_at, at) FROM told WHERE topic=? AND feedback IS NOT NULL "
                       "ORDER BY COALESCE(feedback_at, at) DESC LIMIT 5", (topic,)).fetchall()
    return [(r[0], r[1]) for r in rows]


def _already(con, key: str, held_id: str | None = None) -> bool:
    if con.execute("SELECT 1 FROM told WHERE key=?", (key,)).fetchone():
        return True
    q = "SELECT 1 FROM held WHERE key=? AND status='waiting'" + (" AND id != ?" if held_id else "")
    return con.execute(q, (key, held_id) if held_id else (key,)).fetchone() is not None


def _decide(ctx, m: Moment, now: float, age_s: float = 0.0, held_id: str | None = None) -> Decision:
    o = _opts(ctx)
    return decide(m, now=now, presence=presence(ctx), in_session=in_session(ctx.con), told_today=told_today(ctx.con, now),
                  last_told_at=_last_told_at(ctx.con), cap=o["cap"], quiet_from=o["quiet_from"], quiet_to=o["quiet_to"],
                  topic_feedback=_topic_feedback(ctx.con, m.topic), already=_already(ctx.con, m.keyed(), held_id), age_s=age_s)


# ---- acting on a decision

def _ask_text(m: Moment, address: str = "") -> str:
    """The fixed ask. If he asked to be called something (an address way) it ends the first sentence: "..., boss." """
    what = " ".join((m.topic_text or "that").split()).strip(" .")[:110]
    who = f", {address}" if address else ""
    return f"I've got something on {what}{who}. Want to hear it?"


def seen_now(ctx, now: float) -> dict:
    """What the gate could see right now: the facts a decision is made from (saved with every line she says)."""
    p = presence(ctx)
    return {"hour": datetime.fromtimestamp(now, SGT).hour, "idle_s": None if p is None else p.idle_s,
            "voice": None if p is None else p.voice, "in_session": in_session(ctx.con), "told_today": told_today(ctx.con, now)}


async def _deliver(ctx, m: Moment, how: str, why: str, now: float, waited_s: float = 0.0) -> str:
    if how == "say" and m.quiet:
        how = "card"
    said = _ask_text(m, ways.address(ctx.con)) if how == "ask" else m.text
    tid = "td_" + secrets.token_hex(5)
    seen = json.dumps(seen_now(ctx, now))
    with ctx.con:
        ctx.con.execute("INSERT INTO told(id, at, topic, topic_text, key, text, said, source_name, url, how, urgency, why, waited_s, seen) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (tid, now, m.topic, m.topic_text, m.keyed(), m.text, said, m.source_name, m.url, how, m.urgency, why, waited_s, seen))
        ctx.con.execute("INSERT INTO chat_messages(role, text, meta, created_at) VALUES ('assistant', ?, ?, ?)",
                        (said, '{"proactive": "%s", "told": "%s"}' % (how, tid), now))
    log_event("GATE_TOLD", code=how)
    if ctx.notify is not None:
        try:
            await ctx.notify("proactive.say", {"id": tid, "how": how, "text": said, "urgency": m.urgency,
                                               "source_name": m.source_name, "url": m.url})
        except Exception:                                  # the app being away never loses what she said: it is in the chat
            log_event("GATE_NOTIFY_FAILED", code="NOTIFY")
    return tid


async def silent_note(ctx, text: str, meta: dict) -> None:
    """A line in the chat that is never spoken (a lesson she repeats back). Not a "told" line: no cap, no thumbs."""
    now = ctx.clock.now()
    with ctx.con:
        ctx.con.execute("INSERT INTO chat_messages(role, text, meta, created_at) VALUES ('assistant', ?, ?, ?)",
                        (text, json.dumps({"proactive": "note", **meta}), now))
    if ctx.notify is not None:
        try:
            await ctx.notify("proactive.say", {"id": "note", "how": "card", "text": text, "urgency": "later"})
        except Exception:
            log_event("GATE_NOTIFY_FAILED", code="NOTIFY")


async def _resolve(ctx, m: Moment, now: float, age_s: float = 0.0, held_id: str | None = None, checked: bool = False):
    """The hard rules decide (plain code), then the lessons read the moment once. A lesson can drop it, push it later or
    pull it forward, but whatever it decides goes through the hard rules again, so no lesson can make her speak in class,
    on a call, at night or over the daily limit. Returns (decision, moment, checked)."""
    from . import lessons
    d = _decide(ctx, m, now, age_s, held_id)
    if d.action == "drop" or checked or m.plain:
        return d, m, checked or m.plain
    v = await lessons.verdict(ctx, m)           # once per moment: even a held one, so a lesson can move it earlier
    if v is None:
        return d, m, False
    a, _n, text = v
    if a == "skip":
        return Decision("drop", f"lesson: {text}"[:160]), m, True
    if a == "later" and m.urgency != "later":
        m2 = replace(m, urgency="later")
        return _decide(ctx, m2, now, age_s, held_id), m2, True
    if a == "urgent" and m.urgency != "now":
        m2 = replace(m, urgency="now")
        return _decide(ctx, m2, now, age_s, held_id), m2, True
    return d, m, True


def _hold(ctx, m: Moment, why: str, now: float, checked: bool = False) -> str:
    hid = "hd_" + secrets.token_hex(5)
    with ctx.con:
        ctx.con.execute("INSERT INTO held(id, at, expires_at, urgency, key, topic, topic_text, text, source_name, url, why, checked) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (hid, now, now + TTL.get(m.urgency, TTL["soon"]), m.urgency if m.urgency in URGENCIES else "soon",
                         m.keyed(), m.topic, m.topic_text, m.text, m.source_name, m.url, why, 1 if checked else 0))
    return hid


async def submit(ctx, m: Moment, hold: bool = True) -> Decision:
    """A new moment arrives. Decide now: tell it, keep it for a better time, or drop it. hold=False: a moment that is
    only worth saying right now (a class about to start) is dropped, not kept, when the gate says wait."""
    now = ctx.clock.now()
    d, m, checked = await _resolve(ctx, m, now)
    if d.action in ("say", "card", "ask"):
        await _deliver(ctx, m, d.action, d.why, now)
    elif d.action == "wait":
        if hold:
            _hold(ctx, m, d.why, now, checked)
    elif d.why.startswith("lesson:") and hold:            # kept, not lost: the Radar shows what a lesson stopped
        hid = _hold(ctx, m, d.why, now, True)
        with ctx.con:
            ctx.con.execute("UPDATE held SET status='skipped' WHERE id=?", (hid,))
    log_event("GATE_DECIDED", code=d.action)
    return d


def _held_moment(r) -> Moment:
    return Moment(text=r["text"], urgency=r["urgency"], topic=r["topic"], topic_text=r["topic_text"],
                  source_name=r["source_name"], url=r["url"], key=r["key"], plain=r["topic"] in ("brief", "timer"), own=r["topic"] == "timer")


async def release(ctx) -> int:
    """Called every few seconds: try the waiting items again, most urgent first. At most one is told per call,
    and a stale one is dropped quietly."""
    now = ctx.clock.now()
    con = ctx.con
    rows = con.execute("SELECT * FROM held WHERE status='waiting' ORDER BY CASE urgency WHEN 'now' THEN 0 WHEN 'soon' THEN 1 ELSE 2 END, at").fetchall()
    told = 0
    for r in rows:
        if now >= r["expires_at"]:
            with con:
                con.execute("UPDATE held SET status='expired' WHERE id=?", (r["id"],))
            continue
        if told:
            continue
        m = _held_moment(r)
        d, m2, checked = await _resolve(ctx, m, now, held_id=r["id"], checked=bool(r["checked"]))
        if d.action in ("say", "card", "ask"):
            await _deliver(ctx, m2, d.action, d.why, now, waited_s=now - r["at"])
            with con:
                con.execute("UPDATE held SET status='delivered', why=?, checked=1 WHERE id=?", (d.why, r["id"]))
            told += 1
        elif d.action == "drop":
            with con:
                con.execute("UPDATE held SET status=?, why=? WHERE id=?", ("skipped" if d.why.startswith("lesson:") else "expired", d.why, r["id"]))
        else:
            with con:
                con.execute("UPDATE held SET why=?, urgency=?, checked=? WHERE id=?", (d.why, m2.urgency, 1 if checked else 0, r["id"]))
    return told


# ---- an ask, answered in chat

def pending_ask(con, now: float):
    return con.execute("SELECT * FROM told WHERE how='ask' AND feedback IS NULL AND at >= ? ORDER BY at DESC LIMIT 1",
                       (now - ASK_WINDOW_S,)).fetchone()


def ask_block(con, now: float) -> str:
    r = pending_ask(con, now)
    if r is None:
        return ""
    return (f"You asked him whether he wants an update on \"{r['topic_text'][:110]}\" and he has not answered. If his message "
            "is a yes (\"sure\", \"go on\", \"what is it\"), call answer_ask with accepted true and give him what it returns "
            "in one or two spoken sentences, nothing added. If it is a no (\"not now\", \"skip it\"), call answer_ask with "
            "accepted false and say nothing more than \"Okay.\" If his message is about something else, ignore this.")


def answer_ask(ctx, accepted: bool) -> dict:
    now = ctx.clock.now()
    r = pending_ask(ctx.con, now)
    if r is None:
        return {"ok": False, "error": "there is no question waiting for an answer"}
    with ctx.con:
        ctx.con.execute("UPDATE told SET feedback=?, feedback_at=? WHERE id=?", ("accepted" if accepted else "declined", now, r["id"]))
    if not accepted:
        return {"ok": True, "accepted": False}
    return {"ok": True, "accepted": True, "text": r["text"], "source": r["source_name"]}


# ---- options, changed by chat

def set_option(ctx, name: str, value) -> dict:
    field = OPTIONS.get(name)
    if field is None:
        return {"ok": False, "error": "options are daily_limit, quiet_from and quiet_to"}
    try:
        v = int(value)
    except (TypeError, ValueError):
        return {"ok": False, "error": "the value must be a whole number"}
    if field == "cap" and not 0 <= v <= CAP_MAX:
        return {"ok": False, "error": f"the daily limit is between 0 and {CAP_MAX}"}
    if field != "cap" and not 0 <= v <= 23:
        return {"ok": False, "error": "hours are 0 to 23, Singapore time"}
    o = _opts(ctx)
    o[field] = v
    ctx.settings.set("speak_up", o)
    return {"ok": True, **describe(ctx)}


def describe(ctx) -> dict:
    o = _opts(ctx)
    return {"daily_limit": o["cap"], "quiet_from": o["quiet_from"], "quiet_to": o["quiet_to"],
            "told_today": told_today(ctx.con, ctx.clock.now())}
