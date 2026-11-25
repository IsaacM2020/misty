"""One Stream run: observe -> brain -> gate -> act -> see what changed -> log, until done or a limit is hit."""
from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field

from ..errors import CapHit, Fatal, NetworkDown, Retryable
from . import actions, appcards, observe
from .log import RunLog

MAX_STEPS = 20
MAX_USD = 0.10
SAME_LIMIT = 3
TRIES = 2              # an action that changed nothing this many times is refused after that
STUCK = 6              # this many steps in a row with nothing changing ends the run
OPENERS = ("open_path", "open_app", "open_url")     # actions whose success is `opened X`
REPEATS = 3            # the same action this many times, even one that changes the screen, is going in circles


# Words that mean the task wants more than the file open: then the run does not stop at the open.
_MORE = re.compile(r"\b(send|email|e-mail|mail|text|message|delete|remove|edit|write|type|copy|paste|move|rename|print|share|"
                   r"attach|save|close|click|press|fill|solve|answer|summari[sz]e|read|then|after|also|scroll|add|create|change|"
                   r"replace|upload|download|screenshot|translate|check)\b", re.I)


def only_open(task: str) -> bool:
    """V5: the whole job is to get a file open ("open the worksheet", "find it and open it, report its name")."""
    return bool(re.search(r"\bopen\b", task, re.I)) and not _MORE.search(task)


@dataclass
class Result:
    run_id: str
    task: str
    brain: str
    outcome: str                   # done | ask_user | denied | never | step_limit | usd_limit | looped | stuck | error:<code>
    summary: str = ""
    steps: int = 0
    usd: float = 0.0
    secs: float = 0.0
    raw_steps: int = 0
    trace: list[dict] = field(default_factory=list)
    did: list[str] = field(default_factory=list)      # what it opened (a file, an app, a page), kept even when the run did not reach `done`


async def _no_confirm(_: str) -> bool:
    return False


async def run(task: str, *, hands, brain, home, context: str = "", confirm=_no_confirm, on_status=None,
              max_steps: int = MAX_STEPS, max_usd: float = MAX_USD, start_app=None) -> Result:
    rid = "st_" + time.strftime("%Y%m%d-%H%M%S") + "_" + secrets.token_hex(3)
    log = RunLog(home, rid)
    res = Result(rid, task, brain.name, "step_limit")
    t0 = time.time()
    log.write("start", task=task, brain=brain.name, context=context)

    app = start_app or await hands.front_app()
    obs = await observe.observe(hands, app)
    cards: dict[int, str] = {}
    history: list[str] = []
    image: bytes | None = None
    last_effect, nudge, weak_done_asked = "", "", False
    failed: dict[str, int] = {}          # action signature -> times it changed nothing
    still = 0                            # steps in a row that changed nothing
    used: dict[str, int] = {}            # action signature -> times done at all

    async def status(text: str) -> None:
        if on_status:
            await on_status(text)

    for step in range(1, max_steps + 1):
        if obs.app.pid not in cards:
            cards[obs.app.pid] = await appcards.card(hands, obs.app, home)
        msgs = brain.messages(task, context, cards[obs.app.pid], obs.render(), history, image, nudge)
        await actions.show(hands, "think")
        image, nudge = None, ""
        try:
            d = await brain.decide(msgs)
        except (CapHit, Fatal, NetworkDown, Retryable) as e:
            res.outcome = f"error:{e}"
            break
        res.usd += d.usd
        a = d.action
        res.steps = step
        log.write("step", step=step, obs=obs.render(), action=a, usd=d.usd, secs=round(d.secs, 2), tokens_in=d.tokens_in)
        k = a.get("action")
        await status(f"step {step}: {a.get('thought') or k}")

        if k in ("_none", "_bad_args") or k not in actions.ACTIONS:
            history.append(f"{step}. (no valid action: {a.get('said') or k}) -> call the act tool with one action")
            continue
        sig = json.dumps({x: a.get(x) for x in ("action", "id", "text", "path", "keys", "name", "url")}, sort_keys=True)
        res.trace.append(json.loads(sig))
        if len(res.trace) >= SAME_LIMIT and all(t == res.trace[-1] for t in res.trace[-SAME_LIMIT:]) and last_effect.startswith("Nothing"):
            res.outcome = "looped"
            break
        if k == "done":
            if last_effect.startswith("Nothing") and not weak_done_asked:
                weak_done_asked = True
                nudge = "Your last action changed nothing on screen. Check the controls again before calling done."
                history.append(f"{step}. done? -> not accepted yet, the last action changed nothing")
                continue
            res.outcome, res.summary = "done", a.get("evidence", "")
            break
        if k == "look" and len(res.trace) >= 3 and all(t["action"] == "look" for t in res.trace[-3:]):
            history.append(f"{step}. look -> refused: you already have a fresh screenshot. Act on it now.")
            continue
        if k == "ask_user":
            res.outcome, res.summary = "ask_user", a.get("text", "")
            break
        if k != "look" and failed.get(sig, 0) >= TRIES:
            # The "minimise all" run tried Window > Minimize All seven times between other attempts
            history.append(f"{step}. {actions.describe(a, obs)} -> refused: tried {TRIES} times already and nothing "
                           "changed. Do something different, or ask_user.")
            still += 1
            if still >= STUCK:
                res.outcome = "stuck"
                break
            continue
        if k != "look" and used.get(sig, 0) >= REPEATS:
            # the world-record run opened the same Google search 7 times, each one a new tab
            history.append(f"{step}. {actions.describe(a, obs)} -> refused: done {REPEATS} times already, you are "
                           "going in circles. Use what is on screen now (look), call done, or ask_user.")
            still += 1
            if still >= STUCK:
                res.outcome = "stuck"
                break
            continue
        used[sig] = used.get(sig, 0) + 1

        try:
            out = await actions.run(a, obs, hands, confirm)
        except (Fatal, Retryable) as e:
            out = actions.Outcome(f"{k} failed ({e}); try another way")
        res.raw_steps += int(out.raw)
        if k in OPENERS and out.text.startswith("opened "):
            res.did = (res.did + [out.text])[-5:]
        if out.stop:
            res.outcome, res.summary = out.stop, out.text
            log.write("stop", reason=out.text)
            break
        if out.image:
            image = out.image
        before = obs
        try:
            obs = await observe.observe(hands, out.app or obs.app)
        except (Fatal, Retryable):
            try:
                obs = await observe.observe(hands, await hands.front_app())
            except (Fatal, Retryable) as e:
                res.outcome = f"error:{e}"
                break
        last_effect = observe.effect(before, obs) if not out.image else "Screenshot attached."
        if last_effect.startswith("Nothing"):
            failed[sig] = failed.get(sig, 0) + 1
            still += k != "look"
        elif k != "look":
            still = 0
        if still >= STUCK:
            history.append(f"{step}. {actions.describe(a, before)} -> {out.text}. {last_effect}")
            res.outcome = "stuck"
            break
        history.append(f"{step}. {actions.describe(a, before)} -> {out.text}. {last_effect}")
        if k == "open_path" and out.data.get("file") and out.data.get("front_changed") and only_open(task):
            # V5: the file opened and another app came to the front, and opening it was the whole task: done, with that as evidence.
            res.outcome = "done"
            res.summary = f"{out.text}; {obs.app.name} is now in front"
            break
        if res.usd >= max_usd:
            res.outcome = "usd_limit"
            break

    if res.outcome != "done" and not res.summary and res.did:
        # V3: the file was opened, then the run ran out of steps checking it. Never report that as "found nothing".
        res.summary = (f"Stopped ({res.outcome}) before it could confirm. It did this: {'; '.join(res.did)}. "
                       "That may have worked, so check the screen before saying it did not.")
    await actions.show(hands, "hide")
    res.secs = round(time.time() - t0, 1)
    log.write("end", outcome=res.outcome, summary=res.summary, steps=res.steps, usd=round(res.usd, 6), secs=res.secs,
              final_obs=obs.render())
    await status(f"{res.outcome}: {res.summary}" if res.summary else res.outcome)
    return res
