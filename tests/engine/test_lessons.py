"""Lessons (Season 3, Day 7): feedback becomes a short lesson; the gate reads lessons before she speaks; every new or edited
lesson is replayed against all saved mistakes and thumbs-ups and kept only if it fixes its case and breaks none.
The model is replaced by a stand-in that follows simple lesson wording, so these tests check our code, not the model."""
import json
import re

import pytest

from misty_engine import chat, feedback, gate, lessons
from misty_engine.gate import Moment
from test_chat import say, call
from test_gate import T, g  # noqa: F401


class Judge:
    """Stands in for the model. verdict calls follow the lessons' wording; learn/revise calls play back a script."""
    def __init__(self, *scripted):
        self.scripted, self.verdict_calls, self.other_calls, self.bodies = list(scripted), 0, 0, []

    async def post(self, path, body, **kw):
        self.bodies.append(body)
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if system.startswith("You decide whether Misty"):
            self.verdict_calls += 1
            head, cases_part = user.split("\n\nCASES:\n")
            ls = dict((int(n), t) for n, t in re.findall(r"^(\d+)\. (.+)$", head, re.M))
            out = []
            for n, line in enumerate(cases_part.split("\n"), 1):
                text = re.search(r'says: "(.*?)" \|', line).group(1).lower()
                a, which = "tell", 0
                for k, t in ls.items():
                    m = re.search(r"don't tell him about (\w+)", t.lower())
                    if m and m.group(1) in text:
                        a, which = "skip", k
                    m = re.search(r"wait on (\w+)", t.lower())
                    if m and m.group(1) in text and a == "tell":
                        a, which = "later", k
                    m = re.search(r"straight away about (\w+)", t.lower())
                    if m and m.group(1) in text and a == "tell":
                        a, which = "urgent", k
                out.append({"n": n, "a": a, "lesson": which})
            return {"choices": [{"message": {"content": json.dumps({"d": out})}}], "usage": {"cost": 0.0003}}
        self.other_calls += 1
        reply = self.scripted.pop(0) if self.scripted else {"action": "none", "edit": 0, "text": ""}
        return {"choices": [{"message": {"content": json.dumps(reply)}}], "usage": {"cost": 0.0003}}


@pytest.fixture
def L(g):
    g.settings.set("paid_calls_allowed", True)
    g.net.online = True
    return g


async def tell(ctx, text, key, topic="f1", topic_text="India vs NZ", u="soon"):
    await gate.submit(ctx, Moment(text, u, topic=topic, topic_text=topic_text, source_name="Cricinfo", key=key))
    ctx.clock.t += gate.MIN_GAP_S + 1
    gate.set_presence(ctx, 300.0)
    return ctx.con.execute("SELECT id FROM told WHERE key=?", (key,)).fetchone()[0]


def add_lesson(ctx, text):
    ctx.con.execute("INSERT INTO lessons(id, text, created_at, updated_at) VALUES (?,?,0,0)", ("ls_" + str(abs(hash(text)) % 10**6), text))
    ctx.con.commit()


# ---- the live check in the gate

@pytest.mark.asyncio
async def test_with_no_lessons_the_model_is_never_asked(L):
    L.openrouter = Judge()
    assert (await gate.submit(L, Moment("Cricinfo reports rain.", "soon", topic="f1", key="a"))).action == "say"
    assert L.openrouter.verdict_calls == 0 and not L.openrouter.bodies


@pytest.mark.asyncio
async def test_a_lesson_can_stop_a_line_and_the_radar_still_shows_what_it_stopped(L):
    add_lesson(L, "Don't tell him about rain delays")
    L.openrouter = Judge()
    d = await gate.submit(L, Moment("Cricinfo reports a rain delay.", "soon", topic="f1", key="a"))
    assert d.action == "drop" and d.why.startswith("lesson: Don't tell him about rain")
    assert L.con.execute("SELECT count(*) FROM told").fetchone()[0] == 0
    assert L.con.execute("SELECT status FROM held").fetchone()[0] == "skipped"
    assert (await gate.submit(L, Moment("Cricinfo reports India won.", "soon", topic="f1", key="b"))).action == "say"


@pytest.mark.asyncio
async def test_later_waits_for_a_long_break_and_is_not_asked_twice(L):
    add_lesson(L, "Wait on scorecard")
    L.openrouter = Judge()
    gate.set_presence(L, 60.0)
    d = await gate.submit(L, Moment("Cricinfo posts the scorecard.", "soon", topic="f1", key="a"))
    assert d.action == "wait" and L.con.execute("SELECT urgency, checked FROM held").fetchone()[:] == ("later", 1)
    gate.set_presence(L, 200.0)
    assert await gate.release(L) == 1
    assert L.openrouter.verdict_calls == 1                      # the release did not ask again


@pytest.mark.asyncio
async def test_urgent_moves_a_line_up_but_never_past_the_hard_rules(L):
    add_lesson(L, "Tell him straight away about marks")
    L.openrouter = Judge()
    gate.set_presence(L, 15.0)                                  # a short pause: "soon" would wait
    d = await gate.submit(L, Moment("Ms Smitha posted your marks.", "soon", topic="m", key="a"))
    assert d.action == "say"
    from misty_engine import classes
    classes.start(L.con, L.clock, "Physics", None, None)         # in class: nothing gets through
    L.clock.t += gate.MIN_GAP_S + 1
    gate.set_presence(L, 300.0)
    d = await gate.submit(L, Moment("Ms Smitha posted more marks.", "soon", topic="m", key="b"))
    assert d.action == "wait" and d.why == "in class or on a call"


@pytest.mark.asyncio
async def test_if_the_check_cannot_run_she_goes_ahead_rather_than_going_silent(L):
    add_lesson(L, "Don't tell him about rain")
    L.settings.set("paid_calls_allowed", False)
    L.openrouter = Judge()
    assert (await gate.submit(L, Moment("Cricinfo reports rain.", "soon", topic="f1", key="a"))).action == "say"
    assert L.con.execute("SELECT count(*) FROM told").fetchone()[0] == 1 and not L.openrouter.bodies


# ---- learning from a mistake, with the replay

async def rated_down(ctx, text, key="a", note="not useful"):
    tid = await tell(ctx, text, key)
    feedback.rate(ctx, tid, "down", note)
    return tid


@pytest.mark.asyncio
async def test_a_thumbs_down_becomes_a_lesson_that_fixes_the_case_and_breaks_nothing(L):
    liked = await tell(L, "Cricinfo reports play has resumed.", "u")
    feedback.rate(L, liked, "up")
    await rated_down(L, "Cricinfo reports a rain delay at Wellington.")
    L.openrouter = Judge({"action": "add", "edit": 0, "text": "Don't tell him about delay news; tell him when play starts."})
    r = await lessons.learn(L)
    assert r["ok"] and "delay" in r["lesson"] and r["passed"] == r["replayed"] == 2
    assert [x["text"] for x in lessons.as_list(L.con)] == [r["lesson"]]
    assert L.con.execute("SELECT status, lesson_id FROM mistakes").fetchone()[:] == ("learned", r["id"])
    assert L.con.execute("SELECT verdict FROM replays").fetchone()[0] == "kept"
    note = L.con.execute("SELECT text, meta FROM chat_messages ORDER BY id DESC LIMIT 1").fetchone()
    assert note["text"].startswith("Lesson learned:") and json.loads(note["meta"])["proactive"] == "note"
    assert await lessons.learn(L) is None                       # nothing left to learn from


@pytest.mark.asyncio
async def test_after_learning_the_same_kind_of_line_is_held_back_and_the_liked_one_still_comes(L):
    await rated_down(L, "Cricinfo reports a rain delay at Wellington.")
    L.openrouter = Judge({"action": "add", "edit": 0, "text": "Don't tell him about delay news."})
    await lessons.learn(L)
    L.clock.t += gate.MIN_GAP_S + 1
    gate.set_presence(L, 300.0)
    assert (await gate.submit(L, Moment("Cricinfo reports another delay.", "soon", topic="f9", key="n1"))).action == "drop"
    assert (await gate.submit(L, Moment("Cricinfo reports India won by 5 wickets.", "soon", topic="f9", key="n2"))).action == "say"


@pytest.mark.asyncio
async def test_a_lesson_that_breaks_a_liked_line_is_revised_once_then_kept(L):
    liked = await tell(L, "Cricinfo reports rain stopped, play resumed.", "u")
    feedback.rate(L, liked, "up")
    await rated_down(L, "Cricinfo reports a rain delay at Wellington.")
    L.openrouter = Judge({"action": "add", "edit": 0, "text": "Don't tell him about rain."},         # too broad: skips the liked line
                         {"action": "add", "edit": 0, "text": "Don't tell him about delay news."})  # the revision
    r = await lessons.learn(L)
    assert r["ok"] and r["lesson"] == "Don't tell him about delay news." and L.openrouter.other_calls == 2


@pytest.mark.asyncio
async def test_a_lesson_that_cannot_be_fixed_is_rejected_and_nothing_changes(L):
    liked = await tell(L, "Cricinfo reports rain stopped, play resumed.", "u")
    feedback.rate(L, liked, "up")
    await rated_down(L, "Cricinfo reports a rain delay at Wellington.")
    L.openrouter = Judge({"action": "add", "edit": 0, "text": "Don't tell him about rain."},
                         {"action": "add", "edit": 0, "text": "Don't tell him about rain news."})
    r = await lessons.learn(L)
    assert r["ok"] is False and r["rejected"] and any("resumed" in b for b in r["broken"])
    assert lessons.as_list(L.con) == [] and L.con.execute("SELECT status FROM mistakes").fetchone()[0] == "unresolved"
    assert L.con.execute("SELECT verdict FROM replays").fetchone()[0] == "rejected"
    assert "break" in L.con.execute("SELECT text FROM chat_messages ORDER BY id DESC LIMIT 1").fetchone()[0]


@pytest.mark.asyncio
async def test_a_single_ignored_line_teaches_nothing_but_two_on_one_topic_do(L):
    await tell(L, "Cricinfo reports a rain delay.", "a")
    L.clock.t += feedback.IGNORE_AFTER_S + 10
    feedback.sweep_ignored(L)
    L.openrouter = Judge()
    assert await lessons.learn(L) is None and not L.openrouter.bodies
    L.clock.t -= feedback.IGNORE_AFTER_S                          # a second, fresher line
    gate.set_presence(L, 300.0)
    await tell(L, "Cricinfo reports a second delay.", "b")
    L.clock.t += feedback.IGNORE_AFTER_S + 10
    feedback.sweep_ignored(L)
    L.openrouter = Judge({"action": "add", "edit": 0, "text": "Don't tell him about delay news."})
    assert (await lessons.learn(L))["ok"]


@pytest.mark.asyncio
async def test_missed_teaches_a_lesson_but_not_one_that_hides_what_he_missed(L):
    feedback.record(L, "missed", "the Lakers game moved to Friday", "you should've told me")
    L.openrouter = Judge({"action": "add", "edit": 0, "text": "Don't tell him about Lakers."},
                         {"action": "add", "edit": 0, "text": "Tell him straight away about schedule changes."})
    r = await lessons.learn(L)
    assert r["ok"] and "schedule changes" in r["lesson"]


@pytest.mark.asyncio
async def test_no_budget_means_no_learning_and_nothing_is_lost(L):
    await rated_down(L, "Cricinfo reports a rain delay.")
    L.settings.set("paid_calls_allowed", False)
    L.openrouter = Judge()
    assert await lessons.learn(L) is None
    assert L.con.execute("SELECT status FROM mistakes").fetchone()[0] == "open"


# ---- by chat

@pytest.mark.asyncio
async def test_she_can_add_edit_and_drop_a_lesson_by_chat_and_each_is_checked(L):
    L.openrouter = Judge()
    L.openrouter.scripted = []
    brain = Judge()
    from test_chat import ScriptBrain

    class Both(ScriptBrain):
        async def post(self, path, body, **kw):
            if body["messages"][0]["content"] if isinstance(body["messages"][0]["content"], str) else False:
                if body["messages"][0]["content"].startswith("You decide whether Misty"):
                    return await brain.post(path, body, **kw)
            return await super().post(path, body, **kw)

    L.openrouter = Both({"calls": [call("lesson", action="add", text="Don't tell him about transfers.")]}, {"content": "Done."},
                        {"calls": [call("lesson", action="edit", number=1, text="Don't tell him about rumours.")]}, {"content": "Done."},
                        {"calls": [call("lesson", action="drop", number=1)]}, {"content": "Gone."})
    await say(L, "don't tell me about transfers")
    assert [x["text"] for x in lessons.as_list(L.con)] == ["Don't tell him about transfers."]
    assert "1. Don't tell him about transfers." in chat.system_parts(L, "x")[0]
    await say(L, "change lesson 1 to rumours")
    assert [x["text"] for x in lessons.as_list(L.con)] == ["Don't tell him about rumours."]
    await say(L, "forget lesson 1")
    assert lessons.as_list(L.con) == []
    assert [r["verdict"] for r in L.con.execute("SELECT verdict FROM replays ORDER BY at, rowid")] == ["kept", "kept", "dropped"]


def test_lesson_edits_that_make_no_sense_are_refused(L):
    assert lessons.drop(L, 3)["ok"] is False
    assert lessons.block(L.con).endswith("(none yet)")


@pytest.mark.asyncio
async def test_the_exam_prompt_has_no_lessons_and_the_rpc_lists_them(L):
    add_lesson(L, "Don't tell him about rain")
    fixed, _ = chat.system_parts(L, "x", exam=True)
    assert "Your lessons about when" not in fixed and "lesson add" not in fixed
    from misty_engine.api import build_methods
    r = await build_methods(L)["lessons.list"]({})
    assert r["lessons"][0]["n"] == 1 and r["lessons"][0]["text"] == "Don't tell him about rain" and r["last_replay"] is None


@pytest.mark.asyncio
async def test_the_radar_lists_are_the_same_data_she_reads_out_loud(L):
    """Day 9: what the Radar tab shows is built from the same rows as the lists in her prompt, so they cannot disagree."""
    from misty_engine import cares, follows
    from misty_engine.api import build_methods
    cares.set_care(L, "cricket", "loves cricket", "interest", "high")
    follows.add(L, "India vs NZ", "web", "india nz odi", None, 30, 24)
    add_lesson(L, "Don't tell him about rain")
    m = build_methods(L)
    c, f, ls = await m["cares.list"]({}), await m["follows.list"]({}), await m["lessons.list"]({})
    fixed, _ = chat.system_parts(L, "x")
    assert all(x["key"] in fixed and x["text"] in fixed for x in c["cares"])
    assert all(x["text"] in fixed for x in f["follows"])
    assert all(f"{x['n']}. {x['text']}" in fixed for x in ls["lessons"])
