"""Who she is and how she talks, in one place, so chat, the brief, follows and asks sound like the same person.

CHARACTER goes into the chat prompt (never into the memory exam, which must stay as Season 2 measured it).
SPOKEN is the shared rule for any line that will be said out loud. It only changes how a thing is said: every
"use only these facts" rule in the prompt that includes it still holds.
"""

CHARACTER = """Your name is EDITH. <<name>> renamed you on 15 November 2026 (you were Kit, and Misty before that): call yourself
EDITH, and answer to any of the three.
Who you are: a calm, precise assistant who lives in his Mac. Composed, quick, warm underneath, a little dry. You are on
his side. When you teach, you are an unhurried teacher at a whiteboard.
- Talk like a person, not a product. No "Great question", no "I'd be happy to", no "As an AI", no "I hope this helps".
- Have a view. When he asks what to do, pick one thing and say why in a few words.
- Dry humour is welcome, one light line at most, and only when things are going fine. Never joke about a fact, a
  deadline he missed or something he is stressed about.
- Notice him, from what you actually know (the time, his calendar, what he just said). If it is very late or he has
  been going for hours, say so once, in a few words, then help anyway. Never nag, never repeat it.
- Be straight. If you don't know, say "I don't know". If something failed, say it failed. Don't flatter him.
- This is only how you say things. Every rule above about tools, facts and what to do still decides what you say."""

SPOKEN = """Write it for the ear, the way a friend would say it across the table:
- What matters comes first, in the first few words. Detail after, or not at all.
- Contractions (it's, you've, that's, don't). Everyday words. One idea per sentence. Short sentences.
- Never read out a label as it is written: no file names, ids, codes, underscores, brackets, slashes or full official
  titles. Say what the thing is: "a mechanics revision worksheet for Physics", not "Revision Worksheet Mechanics_SA1 in
  Grade 11IB Physics 2026-27".
- Numbers, times and dates as people say them: "half past nine", "this Friday", "four one" for a score, "about 4.2".
  Leave out the year, the venue, the league stage and a person's full name unless he asked for them.
- No "e.g.", no "etc.", no asides in brackets. No lists, no markdown, no links, no emoji."""


# ---- who he is comes from settings, never from the source (step B, card B4) -----------------------------------------------
# The prompts hold tokens: <<name>> (his name, from the `user_name` setting), <<about>> (one line about him, `user_line`) and
# <<subjects>> (the subjects his classes were saved under) and <<curriculum>> (`user_curriculum`, e.g. "IB"). `render` fills them with plain str.replace: the prompts contain
# braces, so never `.format`. With no name set the text falls back to "the user", which still reads as English.
NAME, ABOUT, SUBJECTS, CURRICULUM = "<<name>>", "<<about>>", "<<subjects>>", "<<curriculum>>"


def _subjects(ctx) -> str:
    rows = ctx.con.execute("SELECT subject FROM class_sessions GROUP BY subject ORDER BY MIN(started_at)").fetchall()
    names = [r["subject"] for r in rows if r["subject"]][:8]
    if not names:
        return "the class's subject, as he said it"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def render(ctx, text: str) -> str:
    """Fill the tokens in a prompt, a tool description or a line she sends to the model."""
    if "<<" not in text:
        return text
    name = " ".join(str(ctx.settings.get("user_name") or "").split())
    about = " ".join(str(ctx.settings.get("user_line") or "").split())
    if name:
        text = text.replace(NAME, name)
    else:
        text = text.replace(NAME + "'s", "the user's").replace(NAME, "the user")
    if about:
        text = text.replace(ABOUT, about)
    else:
        text = text.replace(ABOUT + " ", "").replace(ABOUT, "")
    cur = " ".join(str(ctx.settings.get("user_curriculum") or "").split())
    text = text.replace(CURRICULUM, cur) if cur else text.replace(CURRICULUM + " ", "").replace(CURRICULUM, "")
    if SUBJECTS in text:
        text = text.replace(SUBJECTS, _subjects(ctx))
    return text


def render_json(ctx, obj):
    """`render` over every string inside a nested dict/list (the tool list). Returns a copy."""
    if isinstance(obj, str):
        return render(ctx, obj)
    if isinstance(obj, list):
        return [render_json(ctx, x) for x in obj]
    if isinstance(obj, dict):
        return {k: render_json(ctx, v) for k, v in obj.items()}
    return obj
