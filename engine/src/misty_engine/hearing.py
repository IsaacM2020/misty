"""Live mode's ear for who is being spoken to. The mic is always on, so every phrase the app hears that isn't plainly
for Misty comes here: one Jev call says whether Isaac was talking to Misty, to someone else, or nobody (a video, the
room), whether the sentence was finished, and whether it holds a to-do for him. A to-do heard in someone else's talk
("Isaac, dinner's at 8") is saved as a fact he can see and delete. Nothing else of other people's talk is kept.
"""
from __future__ import annotations

import time

from . import facts
from .errors import MistyError
from .jev_wire import Question
from .logs import log_event
from .openrouter import user_approved
from .scrub import scrub_text

DAILY_CAP = 0.05          # USD a day for hearing; past it only phrases with her name reach her (~500+ phrases a day)
SURE = 0.6                # below this confidence, a phrase is not for Misty: staying quiet beats butting in
CUT_SURE = 0.9            # Jev called finished questions "cut off" at lower confidence

TO = Question("to", "The microphone of Misty, Isaac's assistant on his Mac, heard the phrase below in Isaac's room. "
              "Who is the speaker talking to?", ("misty", "someone_else", "nobody"))
FINISHED = Question("finished", "Is the heard phrase a complete sentence or request, not cut off mid-thought?",
                    ("yes", "no"))
TODO = Question("todo", "Does the heard phrase tell Isaac to do something, or give him a time, date, deadline or plan "
                "he should remember?", ("yes", "no"))

HINTS = {"misty": "The speaker is asking or telling Misty, an AI assistant, to do or answer something.",
         "someone_else": "The speaker is talking with another person in the room or on a call (family, a friend, "
                         "a teacher), not to the assistant.",
         "nobody": "It is background: a video, music, TV, a class recording, or words not aimed at anyone."}


def _context(text: str, last_reply: str, since_reply: float | None) -> str:
    lines = []
    if last_reply and since_reply is not None and since_reply < 120:
        lines.append(f"Misty last said ({int(since_reply)} s ago): {last_reply[:300]}")
    lines.append(f"Heard just now: {text}")
    return "\n".join(lines)


async def hear(ctx, text: str, *, last_reply: str = "", since_reply: float | None = None) -> dict:
    """{to: misty|someone_else|nobody|unsure, finished: bool, todo_saved: bool}."""
    text = scrub_text(text.strip()[:600], "message").text
    if not text or ctx.jev is None:
        return {"to": "unsure", "finished": True, "todo_saved": False}
    if ctx.spend.spent_purpose("hear", "live", None) >= DAILY_CAP:
        log_event("HEAR_CAPPED")
        return {"to": "unsure", "finished": True, "todo_saved": False}
    t0 = time.perf_counter()
    try:
        with user_approved():
            a = await ctx.jev.ask(_context(text, last_reply, since_reply), [TO, FINISHED, TODO], job_id=None,
                                  phase="live", purpose="hear", hints=HINTS)
    except MistyError as e:
        log_event("HEAR_FAILED", code=str(e)[:60])
        return {"to": "unsure", "finished": True, "todo_saved": False}
    to = a["to"].choice[0] if a["to"].confidence >= SURE else "unsure"
    saved = False
    if to != "misty" and a["todo"].choice == ("yes",) and a["todo"].confidence >= SURE:
        when = time.strftime("%a %d %b %H:%M", time.localtime(ctx.clock.now()))
        try:                                       # keyed by its words, so the same to-do heard twice is one fact
            facts.remember(ctx, "heard: " + " ".join(text.split()[:6]), f"Overheard {when} (to-do for Isaac): {text}")
            saved = True
        except MistyError:
            pass
    log_event("HEAR", kind=to, conf=round(a["to"].confidence, 2), finish=a["finished"].choice[0],
              keep=saved, ms=int((time.perf_counter() - t0) * 1000))
    # "cut off" only when Jev is very sure: a wrong "cut off" makes her wait seconds on a finished question
    cut = a["finished"].choice == ("no",) and a["finished"].confidence >= CUT_SURE
    return {"to": to, "finished": not cut, "todo_saved": saved}
