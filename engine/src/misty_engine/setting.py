"""Settings by talking (step B, card B6). One tool, a fixed whitelist, nothing else can be reached from chat.

  name            kept by                          applied by
  speak_replies   the app (UserDefaults)           the app, over the hands wire (op `setting_apply`)
  voice           the app (UserDefaults)           the app, same
  daily_budget    engine setting `cap_daily_usd`   the engine, here
  ambient         the app (this run only)          the app, same: off, name (answers only to her name or the follow-up window) or judge (name, plus a model that decides the rest; only after it passed its test)
  collect_phrases engine setting `collect_phrases` (off by default): keep the words of what the mic heard, on this Mac, for 14 days (F6)
  teach_budget    engine setting `purpose_caps_daily.teach` (what teaching may spend a day), the engine, here

The app answers with what it now holds, so `verified` means "read back from where it is kept", never "I sent the request".
Raising the daily budget asks him first (a yes/no, with the exact numbers); lowering it does not.
"""
from __future__ import annotations

from .errors import Fatal, MistyError
from .logs import log_event

NAMES = ("speak_replies", "voice", "daily_budget", "private_app", "activity_trail", "teach_budget", "ambient", "collect_phrases")
AMBIENT_VALUES = ("off", "name", "judge")      # "judge" needs the F7 pass file (addressed.passed)
TEACH_MIN, TEACH_MAX = 0.02, 1.00
BUDGET_MIN, BUDGET_MAX = 0.10, 3.00
_ON, _OFF = {"on", "yes", "true", "1", "speak", "unmute"}, {"off", "no", "false", "0", "mute", "muted", "silent"}


def _money(v) -> float | None:
    try:
        x = float(str(v).strip().lstrip("$").replace(",", ""))
    except ValueError:
        return None
    return round(x, 2) if BUDGET_MIN <= x <= BUDGET_MAX else None


async def _app(ctx, name: str, value) -> dict:
    hands = (ctx.extras or {}).get("hands")
    if hands is None or not getattr(hands, "connected", False):
        return {"ok": False, "error": "the app isn't connected, so that setting can't be changed right now"}
    try:
        d = await hands._call("setting_apply", name=name, value=value)
    except (Fatal, MistyError) as e:
        return {"ok": False, "error": f"the app could not change it ({e})"}
    return {"ok": bool(d.get("ok", True)), **{k: v for k, v in d.items() if k != "ok"}}


async def apply(ctx, name: str, value, confirm, action: str = "", app: str = "") -> dict:
    """{ok, verified, ...} for the model. Never raises for a bad request."""
    name = str(name or "").strip().lower()
    if name not in NAMES:
        return {"ok": False, "error": f"only these can be changed by voice: {', '.join(NAMES)}"}
    if name == "activity_trail":
        v = str(value if value is not None else "").strip().lower()
        if v not in _ON | _OFF:
            return {"ok": False, "error": "value must be on or off"}
        ctx.settings.set("activity_trail", v in _ON)
        log_event("SETTING", code=f"{name}:ok")
        return {"ok": True, "verified": bool(ctx.settings.get("activity_trail")) == (v in _ON), "value": v in _ON}
    if name == "collect_phrases":
        from . import phrases
        v = str(value if value is not None else "").strip().lower()
        if v in ("forget", "clear", "delete"):
            n = phrases.clear(ctx)
            return {"ok": True, "verified": True, "value": phrases.collecting(ctx), "forgot": n}
        if v not in _ON | _OFF:
            return {"ok": False, "error": "value must be on, off, or forget"}
        now = phrases.set_collecting(ctx, v in _ON)
        log_event("SETTING", code=f"{name}:{'on' if now else 'off'}")
        return {"ok": True, "verified": now == (v in _ON), "value": now, "note": f"it {phrases.CONSENT}" if now else ""}
    if name == "private_app":
        from . import privacy
        return await privacy.change(ctx, str(action or "").lower(), str(app or value or ""))
    if name == "speak_replies":
        v = str(value if value is not None else "").strip().lower()
        if v not in _ON | _OFF:
            return {"ok": False, "error": "value must be on or off"}
        r = await _app(ctx, name, v in _ON)
        r["verified"] = bool(r.get("ok")) and r.get("value") == (v in _ON)
        log_event("SETTING", code=f"{name}:{'ok' if r['verified'] else 'bad'}")
        return r
    if name == "ambient":
        v = str(value if value is not None else "").strip().lower()
        v = "off" if v in _OFF else "name" if v in _ON else v
        if v not in AMBIENT_VALUES:
            return {"ok": False, "error": "ambient is on (she answers only to her name) or off"}
        if v == "judge":
            from . import addressed
            if not addressed.passed(ctx):
                log_event("SETTING", code="ambient:judge_refused")
                return {"ok": False, "verified": False, "error": "the judge is not allowed yet: it has to get 97 percent right on its test set first, and it has not. Name only stays."}
        r = await _app(ctx, name, v)
        r["verified"] = bool(r.get("ok")) and r.get("value") == v
        if r.get("ok") and not r["verified"]:
            r["error"] = "it stayed " + str(r.get("value")) + " (a class, a call or no yes to the live consent stops it)"
        log_event("SETTING", code=f"{name}:{'ok' if r['verified'] else 'no'}")
        return r
    if name == "voice":
        want = str(value or "").strip()
        r = await _app(ctx, name, want)
        r["verified"] = want.lower() != "list" and bool(r.get("ok")) and str(r.get("value", "")).lower() == want.lower()
        log_event("SETTING", code=f"{name}:{'ok' if r['verified'] else 'no'}")
        return r
    if name == "teach_budget":
        try:
            new = round(float(str(value).strip().lstrip("$").replace(",", "")), 2)
        except ValueError:
            new = None
        if new is None or not TEACH_MIN <= new <= TEACH_MAX:
            return {"ok": False, "error": f"the teaching budget must be between ${TEACH_MIN:.2f} and ${TEACH_MAX:.2f}"}
        caps = dict(ctx.settings.get("purpose_caps_daily") or {})
        old = float(caps.get("teach", 0.10))
        if abs(new - old) < 0.005:
            return {"ok": True, "verified": True, "value": new, "was": old, "note": "it was already that"}
        if new > old and not await confirm(f"Raise the teaching budget from ${old:.2f} to ${new:.2f} a day?"):
            log_event("SETTING", code="teach_budget:declined")
            return {"ok": False, "declined": True, "verified": False, "value": old, "error": "he said no: the teaching budget is unchanged"}
        caps["teach"] = new
        ctx.settings.set("purpose_caps_daily", caps)
        now = float((ctx.settings.get("purpose_caps_daily") or {}).get("teach", -1))
        log_event("SETTING", code="teach_budget:ok")
        return {"ok": True, "verified": abs(now - new) < 0.005, "value": now, "was": old}
    new = _money(value)                                           # daily_budget
    if new is None:
        return {"ok": False, "error": f"the daily budget must be between ${BUDGET_MIN:.2f} and ${BUDGET_MAX:.2f}"}
    old = float(ctx.settings.get("cap_daily_usd"))
    if abs(new - old) < 0.005:
        return {"ok": True, "verified": True, "value": new, "was": old, "note": "it was already that"}
    if new > old:
        if not await confirm(f"Raise the daily budget from ${old:.2f} to ${new:.2f}?"):
            log_event("SETTING", code="daily_budget:declined")
            return {"ok": False, "declined": True, "verified": False, "value": old, "error": "he said no: the budget is unchanged"}
    ctx.settings.set("cap_daily_usd", new)
    now = float(ctx.settings.get("cap_daily_usd"))
    log_event("SETTING", code="daily_budget:ok")
    return {"ok": True, "verified": abs(now - new) < 0.005, "value": now, "was": old}
