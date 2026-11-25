"""The notes Misty makes when a class ends: one cloud call turns the transcript (and whatever Isaac typed) into a
summary, key points by topic, a Remember list and action items. Every point must name the transcript segments it came
from, and a point naming a segment that doesn't exist is dropped: nothing made up reaches his notes. His own lines are
kept word for word. This runs when he ends the class, so the background-work pause never holds it up.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
from datetime import date, datetime, timedelta

from . import class_audio, classes, subject_words
from .clock import SGT
from .errors import MistyError
from .logs import log_event
from .openrouter import user_approved

MIN_WORDS = 150
WAITS = (2.0, 8.0, 30.0)          # before the 2nd and 3rd tries; the 3rd failure is final
BAD_REPLIES = 2                   # a reply that isn't JSON was paid for: one more try, not three
CAP_USD = 0.05            # USD per class, all tries: a 40-min class on Haiku 5.5 is ~$0.003
KINDS = ("homework", "test", "reading", "bring", "other")
REMEMBER_KINDS = ("definition", "formula", "exam_hint")
_TIMER = re.compile(r"^\s*\[(\d{1,3}):(\d{2})\]\s*")
_sleep = asyncio.sleep

SYSTEM = """You turn a class transcript into complete study notes for Isaac, an IB student.

The transcript comes as numbered segments: [id] mm:ss text. It is speech to text from a classroom, so expect misheard
words; fix obvious ones from context. In an online class each segment starts with who spoke: "Teacher:" is the sound
from the Mac (the teacher, and classmates who speak) and "Isaac:" is Isaac himself. Take lesson content from the
Teacher, and treat Isaac's questions and answers as part of the class too. An in-person class has no labels. His typed notes (if any) come with the class time each line was started.
In an online class the shared screen is read on his Mac as text and comes as "On the shared screen": mm:ss text, only
when the picture changed. It is OCR of slides plus app buttons and names, so ignore the buttons, use it to spell terms,
formulas and numbers right and to name the topic, and never cite it as a segment.

Reply with one JSON object only:
{"title": "the topic of this class in 2 to 5 words, like a chapter name, no subject name and no date",
 "summary": "3-5 sentences on what the class covered",
 "sections": [{"heading": "topic", "points": [{"text": "a key point", "seg": [ids]}]}],
 "remember": [{"kind": "definition|formula|exam_hint", "text": "...", "seg": [ids]}],
 "action_items": [{"text": "what to do", "kind": "homework|test|reading|bring|other",
                   "due": "YYYY-MM-DD or null", "due_text": "the teacher's words for when", "seg": [ids]}],
 "lost": [{"text": "where Isaac got lost, and on what", "seg": [ids]}],
 "next_step": {"text": "the one thing to do next", "seg": [ids]} or null}

Rules:
- Cover the whole class, even when he typed nothing. Group key points by topic, in class order.
- Every point, remember item and action item lists the segment ids it comes from. Never invent content.
- Put each of his typed lines, word for word without its [mm:ss] timer, as a point with "mine": true in the section it
  belongs to. Never reword his lines.
- "exam_hint" is anything the teacher said will be tested or is important for the exam.
- "lost" is only for moments the transcript shows: Isaac asks a question, says "wait", "what" or that he is confused, or
  the teacher has to explain the same thing again. Name the topic, not the feeling. Cite the "Isaac:" line or the
  repeated explanation. At most 4. An in-person class has no labels, so give [] unless a student question is clearly
  audible. Never guess that he was lost; [] is a fine answer.
- "next_step" is ONE concrete thing for Isaac to do next, with a segment that backs it. Prefer the action item due
  soonest; if there are none, re-do the topic in "lost"; if neither exists, null. Never a list.
- Action items are things Isaac must do: homework, a test to prepare for, reading, something to bring. Dates are
  relative to the class date given; if unsure, set due to null and keep due_text."""


def resolve_due(due_text: str, class_date: date) -> str | None:
    """A due date said in class ("next Tuesday", "28 Oct", "tomorrow") as YYYY-MM-DD, or None when it isn't clear."""
    t = " ".join(due_text.lower().replace(",", " ").split())
    if not t:
        return None
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    if m := re.fullmatch(r"(?:by |due |on )?(\d{4})-(\d{2})-(\d{2})", t):
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return None
    t = re.sub(r"^(?:by|due|on|for|before)\s+", "", t)
    if t in ("today",):
        return class_date.isoformat()
    if t in ("tomorrow", "tmr", "tmrw"):
        return (class_date + timedelta(days=1)).isoformat()
    if m := re.fullmatch(r"in (\d{1,2}) days?", t):
        return (class_date + timedelta(days=int(m[1]))).isoformat()
    if m := re.fullmatch(r"(next |this )?(" + "|".join(days) + r")", t):
        want = days.index(m[2])
        ahead = (want - class_date.weekday()) % 7 or 7
        return (class_date + timedelta(days=ahead)).isoformat()
    mon = "(" + "|".join(months) + r")[a-z]*"
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? " + mon, t) or re.fullmatch(mon + r" (\d{1,2})(?:st|nd|rd|th)?", t)
    if m:
        a, b = m.groups()
        day, mo = (int(a), b) if a.isdigit() else (int(b), a)
        for year in (class_date.year, class_date.year + 1):
            try:
                d = date(year, months.index(mo[:3]) + 1, day)
            except ValueError:
                return None
            if d >= class_date:
                return d.isoformat()
    return None


def _mmss(s: float) -> str:
    return f"{int(s) // 60:02d}:{int(s) % 60:02d}"


def _his_lines(text: str) -> list[str]:
    return [_TIMER.sub("", ln).strip() for ln in text.splitlines() if _TIMER.sub("", ln).strip()]


def _segs(raw, valid: set[int]) -> list[int] | None:
    """The point's segment ids if every one is real, else None (the point is dropped)."""
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for x in raw:
        if not isinstance(x, int) or isinstance(x, bool) or x not in valid:
            return None
        out.append(x)
    return out


def check(data: dict, valid: set[int], his: list[str], class_date: date, gaps: list) -> dict:
    """Keep only what the transcript backs, put his lines back word for word, and resolve due dates."""
    sections, unplaced = [], list(his)                # a line typed twice is two lines: each placement uses one up
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        pts = []
        for p in sec.get("points") or []:
            if not isinstance(p, dict) or not isinstance(p.get("text"), str):
                continue
            if p.get("mine"):
                if p["text"].strip() in unplaced:
                    unplaced.remove(p["text"].strip())
                    pts.append({"text": p["text"].strip(), "seg": [], "mine": True})
                continue
            seg = _segs(p.get("seg"), valid)
            if seg and p["text"].strip():
                pts.append({"text": p["text"].strip(), "seg": seg, "mine": False})
        if pts:
            sections.append({"heading": str(sec.get("heading") or "Notes")[:120], "points": pts})
    left = unplaced
    if left:                                         # his lines the model didn't place still belong in his notes
        sections.append({"heading": "Your other notes", "points": [{"text": ln, "seg": [], "mine": True} for ln in left]})
    remember = [{"kind": r["kind"], "text": r["text"].strip(), "seg": seg} for r in data.get("remember") or []
                if isinstance(r, dict) and r.get("kind") in REMEMBER_KINDS and isinstance(r.get("text"), str)
                and r["text"].strip() and (seg := _segs(r.get("seg"), valid))]
    actions = []
    for a in data.get("action_items") or []:
        if not (isinstance(a, dict) and isinstance(a.get("text"), str) and a["text"].strip() and (seg := _segs(a.get("seg"), valid))):
            continue
        due_text = str(a.get("due_text") or "")[:120]
        due = resolve_due(a["due"], class_date) if isinstance(a.get("due"), str) else None
        if due and due < class_date.isoformat():          # due before the class happened: the model lost the year
            due = None
        actions.append({"id": "ca_" + secrets.token_hex(3), "text": a["text"].strip()[:300],
                        "kind": a.get("kind") if a.get("kind") in KINDS else "other",
                        "due": due or resolve_due(due_text, class_date), "due_text": due_text, "seg": seg})
    lost = [{"text": x["text"].strip()[:300], "seg": seg} for x in data.get("lost") or []
            if isinstance(x, dict) and isinstance(x.get("text"), str) and x["text"].strip()
            and (seg := _segs(x.get("seg"), valid))][:4]
    ns = data.get("next_step")
    next_step = ({"text": ns["text"].strip()[:300], "seg": seg} if isinstance(ns, dict) and isinstance(ns.get("text"), str)
                 and ns["text"].strip() and (seg := _segs(ns.get("seg"), valid)) else None)
    return {"title": " ".join(str(data.get("title") or "").split())[:60],
            "summary": str(data.get("summary") or "").strip(), "sections": sections, "remember": remember,
            "action_items": actions, "lost": lost, "next_step": next_step, "gaps": gaps}


def _who(sess: dict, seg: dict) -> str:
    """'Teacher: ' / 'Isaac: ' in an online class (system audio vs the mic); nothing in a room, where one mic hears all."""
    if sess.get("mode") != "online":
        return ""
    return "Teacher: " if seg.get("source") == "mac" else "Isaac: "


def _screen(slides: list[dict]) -> str:
    """The shared-screen text for the model: at most 30 slides of 400 characters, spread over the class."""
    if len(slides) > 30:
        slides = [slides[i * len(slides) // 30] for i in range(30)]
    return "\n".join(f"{_mmss(s['t'])} {s['text'][:400]}" for s in slides)


def _parts(sess: dict, segs: list[dict], class_date: date, slides: list[dict] | None = None) -> list[str]:
    his = sess["his_notes"].strip() or "(he typed nothing)"
    gaps = "; ".join(f"{_mmss(g['from'])}-{_mmss(g['to'] or g['from'])} ({g['reason']})" for g in sess["gaps"])
    kind = "online (labelled Teacher / Isaac)" if sess.get("mode") == "online" else "in person"
    return [f"Subject: {sess['subject']}\nClass: {sess['title']}\nDate: {class_date.isoformat()} "
            f"({class_date.strftime('%A')})\nFormat: {kind}",
            "Transcript:\n" + "\n".join(f"[{s['id']}] {_mmss(s['t_start'])} {_who(sess, s)}{s['text']}" for s in segs),
            *(["On the shared screen:\n" + _screen(slides)] if slides else []),
            "His typed notes:\n" + his,
            "Not heard (lid closed or paused): " + (gaps or "none")]


def _save(con, sid: str, status: str, notes: dict | None = None, cost: float = 0.0) -> None:
    con.execute("UPDATE class_sessions SET status=?, notes_json=COALESCE(?, notes_json), cost=cost+? WHERE id=?",
                (status, json.dumps(notes) if notes is not None else None, cost, sid))


def _wiki_page(sess: dict, class_date: date) -> tuple[str, str]:
    return ("classes/" + re.sub(r"[^a-z0-9]+", "-", sess["subject"].lower()).strip("-") + ".md",
            f"## {sess['title']} ({class_date.isoformat()})")


def forget_in_wiki(ctx, sess: dict) -> None:
    """Take one class's section off its subject page (a class merged into another no longer has a lesson of its own)."""
    if getattr(ctx, "wiki", None) is None:
        return
    rel, head = _wiki_page(sess, datetime.fromtimestamp(sess["started_at"], SGT).date())
    if ctx.wiki.exists(rel):
        text = (ctx.wiki.root / rel).read_text()
        new = re.sub(r"\n" + re.escape(head) + r"\n.*?(?=\n## |\Z)", "", text, flags=re.S)
        if new != text:
            ctx.wiki.write_text(rel, new)


def _remember_in_wiki(ctx, sess: dict, notes: dict, class_date: date) -> None:
    """Each class's summary and Remember list go on the subject's wiki page, so chat and priorities know it."""
    if getattr(ctx, "wiki", None) is None:
        return
    rel, head = _wiki_page(sess, class_date)
    old = (ctx.wiki.root / rel).read_text() if ctx.wiki.exists(rel) else f"# {sess['subject']} classes\n"
    old = re.sub(r"\n" + re.escape(head) + r"\n.*?(?=\n## |\Z)", "", old, flags=re.S)   # regenerate replaces it
    body = "\n".join([head, notes["summary"]] + [f"- {r['text']}" for r in notes["remember"]])
    ctx.wiki.write_text(rel, old.rstrip() + "\n\n" + body + "\n")


def _on_priorities(ctx, sid: str, subject: str, actions: list[dict]) -> None:
    """Every action item goes straight onto Priorities with its due date (Isaac's call: no approve step)."""
    if getattr(ctx, "wiki", None) is None:
        return
    from .wiki import priorities as pr
    if not actions:                                  # a regenerate with none left must not leave the old lines behind
        pr.remove_class(ctx.wiki, ctx.clock, sid)
        return
    ids = pr.add_class_items(ctx.wiki, ctx.clock, [{"text": a["text"], "subject": subject, "due": a["due"],
                                                    "session": sid} for a in actions])
    for a, pid in zip(actions, ids):
        ctx.con.execute("UPDATE class_actions SET priority_id=? WHERE id=?", (pid, a["id"]))


async def _notify(ctx, sid: str, status: str) -> None:
    if getattr(ctx, "notify", None) is not None:
        await ctx.notify("class.notes", {"session": sid, "status": status})


async def _after_class_pass(ctx, sid: str) -> None:
    """An online class with recorded tracks: Whisper reads each one on this Mac, and that transcript replaces the
    live preview before the notes are made. The preview is kept next to the audio for comparing. Runs once."""
    paths = getattr(ctx, "paths", None)
    if paths is None:
        return
    home = paths.home
    d = class_audio.audio_dir(home, sid)
    if not class_audio.has_tracks(home, sid) or (d / "preview.json").exists():
        return
    sess = classes.get(ctx.con, sid)["session"]
    _save(ctx.con, sid, "noting")
    await _notify(ctx, sid, "noting")
    try:
        segs = await asyncio.to_thread(class_audio.read_class, home, sid, sess["started_at"],
                                       subject_words.prompt_for(sess["subject"], home))
    except Exception as e:                                   # no model, bad file: the live preview stays
        log_event("CLASS_AUDIO_FAIL", item_id=sid, code=type(e).__name__)
        return
    if not segs:
        log_event("CLASS_AUDIO_EMPTY", item_id=sid)
        return
    kept = class_audio.outside_recording(classes.get(ctx.con, sid)["segments"],
                                         class_audio.recorded_windows(home, sid, sess["started_at"]))
    segs = sorted([*segs, *({"t_start": g["t_start"], "t_end": g["t_end"], "source": g["source"], "text": g["text"]}
                            for g in kept)], key=lambda x: (x["t_start"], x["source"]))
    old = classes.replace_segments(ctx.con, sid, segs)
    (d / "preview.json").write_text(json.dumps(old))
    log_event("CLASS_AUDIO", item_id=sid, count=len(segs), n=len(old))


async def teach_fill(ctx, sid: str) -> None:
    """E4: his new notes become evidence on the teach map. Whatever goes wrong here, the class stays `noted`."""
    try:
        from .teach import fill
        with user_approved():
            await fill.fill_class(ctx, sid)
    except Exception as e:
        log_event("TEACH_FILL_FAILED", item_id=sid, code=type(e).__name__)


async def make_notes(ctx, sid: str) -> str:
    con = ctx.con
    await _after_class_pass(ctx, sid)
    got = classes.get(con, sid)
    sess, segs = got["session"], got["segments"]
    class_date = datetime.fromtimestamp(sess["started_at"], SGT).date()
    if sum(len(s["text"].split()) for s in segs) < MIN_WORDS:
        _save(con, sid, "too_little")
        await _notify(ctx, sid, "too_little")
        return "too_little"
    spent = sess.get("cost") or 0.0
    if spent >= CAP_USD:
        log_event("CLASS_NOTES_CAP", item_id=sid, usd=spent)
        _save(con, sid, "failed")
        await _notify(ctx, sid, "failed")
        return "failed"
    _save(con, sid, "noting")
    await _notify(ctx, sid, "noting")
    parts = _parts(sess, segs, class_date, got["slides"])
    room = max(2500, min(8000, 1500 + 8 * len(segs)))     # a long class needs a long answer
    res, paid, bad = None, 0.0, 0
    for i in range(len(WAITS)):
        try:
            with user_approved():                    # he ended the class himself: this call is his ask
                res = await ctx.runner.run(SYSTEM, parts, "class_notes", purpose="class", model_key="haiku",
                                           max_tokens=room)
            break
        except MistyError as e:
            log_event("CLASS_NOTES_RETRY", item_id=sid, code=getattr(e, "code", type(e).__name__), attempt=i + 1,
                      usd=getattr(e, "cost_usd", 0.0))
            paid += getattr(e, "cost_usd", 0.0)
            if getattr(e, "code", "") == "JSON_PARSE":
                bad += 1
                if getattr(e, "truncated", False):
                    room = min(12000, room * 2)
            if bad >= BAD_REPLIES or spent + paid >= CAP_USD or i == len(WAITS) - 1:
                break
            await _sleep(WAITS[i])
    if res is None or not isinstance(res.json, dict):
        _save(con, sid, "failed", cost=paid + (res.cost_usd if res is not None else 0.0))   # a reply that came back was paid for
        await _notify(ctx, sid, "failed")
        return "failed"
    notes = check(res.json, {s["id"] for s in segs}, _his_lines(sess["his_notes"]), class_date, sess["gaps"])
    con.execute("DELETE FROM class_actions WHERE session_id=?", (sid,))
    for a in notes["action_items"]:
        con.execute("INSERT INTO class_actions(id, session_id, text, kind, due, due_text, seg_id) VALUES (?,?,?,?,?,?,?)",
                    (a["id"], sid, a["text"], a["kind"], a["due"], a["due_text"], a["seg"][0]))
    _save(con, sid, "noted", notes, paid + res.cost_usd)
    await teach_fill(ctx, sid)
    if notes["title"] and classes.is_default_title(sess):    # "Chem, 14 Nov" becomes "Le Chatelier, 14 Nov"; his own name stays
        sess["title"] = classes.dated(notes["title"], sess["started_at"])
        con.execute("UPDATE class_sessions SET title=? WHERE id=?", (sess["title"], sid))
    _on_priorities(ctx, sid, sess["subject"], notes["action_items"])
    _remember_in_wiki(ctx, sess, notes, class_date)
    log_event("CLASS_NOTES", item_id=sid, usd=res.cost_usd, n=sum(len(s["points"]) for s in notes["sections"]),
              count=len(notes["action_items"]))
    await _notify(ctx, sid, "noted")
    from . import class_reach
    await class_reach.after_class(ctx, sid)         # one line on what is still to do
    return "noted"

