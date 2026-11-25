"""E7: the lesson as a pure machine. step(state, event) -> (state, actions). No db, no model, no clock inside.

The state is plain data (it lives in one `teach_live` row, so a restart changes nothing). The model only words what the actions say.
Phases he can answer in: check (one per step), guided (hints allowed), solo (no hints, no second explanation). Then the recap.

state   {step, phase, way, misses, hints, n, checks[n], wrong, status, offer}
events  start | answer{verdict: right|partly|wrong, needs_open} | stuck{hints_avail} | show_me | other | again | easier | harder
        | stop | resume | need_yes | need_no
actions explain{step,way} ask{phase,step} hint{n} reveal{phase,step} verdict{v} offer_need evidence{kind,phase,step} slip{phase,step}
        side recap finished stopped paused no_hints
Evidence (written by the caller from these actions, never by the model): a check that ends right = right_helped; one `wrong` at most per
step however many misses; guided right = right_helped; solo = right_alone or wrong; the end of the lesson = taught.
"""
from __future__ import annotations

import copy
import re

MAX_WAY = 2                  # plain, picture, example
MAX_MISSES = 3               # the third miss on one step or one guided question ends it
IDLE_PAUSE_SECONDS = 1800


def new_state(n: int, checks: list[bool]) -> dict:
    return {"step": 0, "phase": "check", "way": 0, "misses": 0, "hints": 0, "n": n, "checks": [bool(c) for c in checks][:n],
            "wrong": False, "status": "live", "offer": False}


def _reset(s: dict) -> None:
    s["way"], s["misses"], s["hints"], s["wrong"] = 0, 0, 0, False


def _enter_step(s: dict, acts: list) -> None:
    """Explain from the current step on until a step with a check (or the end: guided)."""
    while True:
        if s["step"] >= s["n"]:
            s["phase"] = "guided"
            _reset(s)
            acts.append({"do": "ask", "phase": "guided", "step": s["step"]})
            return
        acts.append({"do": "explain", "step": s["step"], "way": s["way"]})
        if s["checks"][s["step"]]:
            s["phase"] = "check"
            acts.append({"do": "ask", "phase": "check", "step": s["step"]})
            return
        s["step"] += 1
        _reset(s)


def _next_step(s: dict, acts: list) -> None:
    s["step"] += 1
    _reset(s)
    _enter_step(s, acts)


def _wrong_once(s: dict, acts: list, phase: str, evidence: bool = True) -> None:
    acts.append({"do": "slip", "phase": phase, "step": s["step"]})
    if evidence and not s["wrong"]:
        s["wrong"] = True
        acts.append({"do": "evidence", "kind": "wrong", "phase": phase, "step": s["step"]})


def _finish(s: dict, acts: list) -> None:
    s["phase"], s["status"] = "done", "done"
    acts += [{"do": "recap"}, {"do": "evidence", "kind": "taught", "phase": "solo", "step": 0}, {"do": "finished"}]


def _to_solo(s: dict, acts: list) -> None:
    s["phase"] = "solo"
    _reset(s)
    acts.append({"do": "ask", "phase": "solo", "step": s["step"]})


def _third(s: dict, acts: list, needs_open: bool) -> None:
    """The third miss on one step: lean on a weak need first, or show the answer and go on."""
    if needs_open:
        s["status"], s["offer"] = "paused", True
        acts.append({"do": "offer_need"})
    else:
        acts.append({"do": "reveal", "phase": "check", "step": s["step"]})
        _next_step(s, acts)


def _ask_again(s: dict, acts: list) -> None:
    acts.append({"do": "ask", "phase": s["phase"], "step": s["step"]})


def step(state: dict, event: dict) -> tuple[dict, list]:
    s, acts, e = copy.deepcopy(state), [], event.get("e")
    if e == "resume":
        if s["status"] in ("paused",) and not s["offer"]:
            s["status"] = "live"
            _ask_again(s, acts)
        return s, acts
    if e == "need_yes":
        if s["status"] == "paused" and s["offer"]:
            acts.append({"do": "paused"})
        return s, acts
    if e == "need_no":
        if s["status"] == "paused" and s["offer"]:
            s["status"], s["offer"] = "live", False
            acts.append({"do": "reveal", "phase": "check", "step": s["step"]})
            _next_step(s, acts)
        return s, acts
    if e == "start":
        s["status"] = "live"
        _enter_step(s, acts)
        return s, acts
    if s["status"] != "live":
        return s, acts
    if e == "stop":
        s["status"] = "stopped"
        acts.append({"do": "stopped"})
        return s, acts
    if e == "other":
        return s, [{"do": "side"}]
    ph = s["phase"]
    if e == "again":
        if ph == "check":
            acts.append({"do": "explain", "step": s["step"], "way": s["way"]})
        _ask_again(s, acts)
        return s, acts
    if e == "easier":
        if ph == "check":
            s["way"] = min(s["way"] + 1, MAX_WAY)
            acts.append({"do": "explain", "step": s["step"], "way": s["way"]})
        _ask_again(s, acts)
        return s, acts
    if e == "harder":
        if ph == "check":
            s["step"] = s["n"]
            s["phase"] = "guided"
            _reset(s)
            acts.append({"do": "ask", "phase": "guided", "step": s["step"]})
        elif ph == "guided":
            _to_solo(s, acts)
        else:
            _ask_again(s, acts)
        return s, acts
    if e == "show_me":
        if ph == "check":
            acts.append({"do": "reveal", "phase": "check", "step": s["step"]})
            _next_step(s, acts)
        elif ph == "guided":
            acts.append({"do": "reveal", "phase": "guided", "step": s["step"]})
            _to_solo(s, acts)
        elif ph == "solo":
            _wrong_once(s, acts, "solo")
            acts.append({"do": "reveal", "phase": "solo", "step": s["step"]})
            _finish(s, acts)
        return s, acts
    if e == "stuck":
        if ph == "solo":
            return s, [{"do": "no_hints"}]
        avail = int(event.get("hints_avail", 3))
        if s["hints"] >= avail or s["hints"] >= MAX_MISSES:
            return step(state, {"e": "show_me"})
        acts.append({"do": "hint", "n": s["hints"]})
        s["hints"] += 1
        _ask_again(s, acts)
        return s, acts
    if e == "answer":
        v = event.get("verdict")
        needs_open = bool(event.get("needs_open"))
        if ph == "check":
            if v == "right":
                acts.append({"do": "evidence", "kind": "right_helped", "phase": "check", "step": s["step"]})
                _next_step(s, acts)
            else:
                s["misses"] += 1
                _wrong_once(s, acts, "check")
                if s["misses"] >= MAX_MISSES:
                    _third(s, acts, needs_open)
                else:
                    s["way"] = min(s["way"] + 1, MAX_WAY)
                    acts.append({"do": "explain", "step": s["step"], "way": s["way"]})
                    _ask_again(s, acts)
        elif ph == "guided":
            if v == "right":
                acts.append({"do": "evidence", "kind": "right_helped", "phase": "guided", "step": s["step"]})
                _to_solo(s, acts)
            else:
                s["misses"] += 1
                _wrong_once(s, acts, "guided", evidence=False)
                if s["misses"] >= MAX_MISSES:
                    acts.append({"do": "reveal", "phase": "guided", "step": s["step"]})
                    _to_solo(s, acts)
                else:
                    acts.append({"do": "hint", "n": min(s["misses"] - 1, MAX_MISSES - 1)})
                    s["hints"] = max(s["hints"], s["misses"])
                    _ask_again(s, acts)
        elif ph == "solo":
            if v == "right":
                acts.append({"do": "evidence", "kind": "right_alone", "phase": "solo", "step": s["step"]})
            else:
                _wrong_once(s, acts, "solo")
                acts.append({"do": "reveal", "phase": "solo", "step": s["step"]})
            _finish(s, acts)
        return s, acts
    return s, acts


# ---- what he said, as an event ---------------------------------------------------------------------------------------
_STOP = re.compile(r"^(?:ok(?:ay)?[ ,]*)?(?:stop|end|quit)(?: (?:it|this|the lesson|here|the class))?\W*$|^(?:let'?s|i want to|can we|i'?ll) stop(?: here| now| for now)?\W*$"
                   r"|^that'?s (?:enough|all) (?:for now|for today)?\W*$|^end (?:the )?lesson\W*$", re.I)
_STUCK = re.compile(r"\b(i(?: do not|'?m not|'?m|am)? (?:don'?t know|stuck|lost|confused|not sure)|i don'?t know|no idea|hint|stuck|i don'?t get it|help me)\b", re.I)
_SHOW = re.compile(r"\b(show me|tell me the answer|what'?s the answer|what is the answer|give up|i give up|just tell me|reveal)\b", re.I)
_AGAIN = re.compile(r"^(?:say|explain|go over|read)(?: that| it| this)? again\W*$|^(?:again|repeat that|one more time|once more)\W*$", re.I)
_EASIER = re.compile(r"\b(easier|simpler|another way|different way|explain (?:it )?differently|explain it another way|in a different way)\b", re.I)
_HARDER = re.compile(r"\b(harder|too easy|skip ahead|skip this|i know this|move on to the hard)\b", re.I)
_QUESTION_NO_NUMBER = re.compile(r"\?\s*$")


def classify(text: str) -> str | None:
    """A control event from his words, or None when it should be graded as an answer. `other` is a side question."""
    t = " ".join((text or "").split())
    if not t:
        return None
    words = len(t.split())
    if words <= 8 and _STOP.search(t):
        return "stop"
    if words <= 10 and _AGAIN.search(t):
        return "again"
    if words <= 12 and _SHOW.search(t):
        return "show_me"
    if words <= 10 and _STUCK.search(t):
        return "stuck"
    if words <= 12 and _HARDER.search(t):
        return "harder"
    if words <= 12 and _EASIER.search(t):
        return "easier"
    if _QUESTION_NO_NUMBER.search(t) and not re.search(r"\d", t):
        return "other"
    return None
