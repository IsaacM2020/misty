"""Notes after class: full notes even with nothing typed, every point backed by a real segment, his lines kept word
for word, no paid call for an empty class, bad replies retried then failed, due dates resolved in SGT."""
import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from misty_engine import class_notes, classes
from misty_engine.errors import Retryable
from misty_engine.runner import RunResult

FRI = 1_792_720_800.0     # 2026-10-23 10:00 SGT, a Friday


class FakeRunner:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def run(self, system, parts, schema_name, **kw):
        self.calls.append({"system": system, "parts": parts, "schema": schema_name, **kw})
        r = self.replies.pop(0) if self.replies else NOTES
        if isinstance(r, Exception):
            raise r
        return RunResult(text=json.dumps(r), json=r, cost_usd=0.01)


NOTES = {
    "summary": "Equilibrium and Le Chatelier's principle.",
    "sections": [{"heading": "Le Chatelier", "points": [
        {"text": "A stress shifts the equilibrium to oppose it.", "seg": [1]},
        {"text": "Catalysts change nothing about the position.", "seg": [999]},
        {"text": "le chat = stress → shift", "mine": True}]}],
    "remember": [{"kind": "definition", "text": "Dynamic equilibrium: forward rate equals reverse rate.", "seg": [2]},
                 {"kind": "formula", "text": "Made up", "seg": [500]}],
    "action_items": [{"text": "Questions 1 to 8 on page 112", "kind": "homework", "due": None,
                      "due_text": "next Tuesday", "seg": [3]},
                     {"text": "Invented task", "kind": "homework", "due": "2026-10-30", "seg": [77]}],
}


@pytest.fixture
def ctx(con, clock, settings):
    clock.t = FRI
    return SimpleNamespace(con=con, clock=clock, settings=settings, runner=FakeRunner(), wiki=None, notify=None)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_s):
        return None
    monkeypatch.setattr(class_notes, "_sleep", instant)


def lesson(ctx, words=200):
    s = classes.start(ctx.con, ctx.clock, "Chem", None, None)["id"]
    filler = " ".join(["equilibrium"] * (words // 3))
    classes.add_segment(ctx.con, s, 0, 10, "room", "Le Chatelier says a stress shifts it " + filler)
    classes.add_segment(ctx.con, s, 10, 20, "room", "dynamic equilibrium means equal rates " + filler)
    classes.add_segment(ctx.con, s, 20, 30, "room", "homework questions one to eight page 112 next Tuesday " + filler)
    classes.end(ctx.con, ctx.clock, s)
    return s


def run(coro):
    return asyncio.run(coro)


def notes_of(ctx, s):
    return classes.get(ctx.con, s)["session"]["notes"]


def all_points(n):
    return [p for sec in n["sections"] for p in sec["points"]]


def test_full_notes_even_with_no_typed_notes(ctx):
    s = lesson(ctx)
    assert run(class_notes.make_notes(ctx, s)) == "noted"
    n = notes_of(ctx, s)
    assert n["summary"].startswith("Equilibrium")
    assert "(he typed nothing)" in "\n".join(ctx.runner.calls[0]["parts"])
    call = ctx.runner.calls[0]
    assert call["purpose"] == "class" and call["model_key"] == "haiku" and call["schema"] == "class_notes"


def test_points_with_unknown_seg_are_dropped(ctx):
    s = lesson(ctx)
    run(class_notes.make_notes(ctx, s))
    n = notes_of(ctx, s)
    texts = [p["text"] for p in all_points(n)]
    assert "A stress shifts the equilibrium to oppose it." in texts
    assert "Catalysts change nothing about the position." not in texts
    assert [r["text"] for r in n["remember"]] == ["Dynamic equilibrium: forward rate equals reverse rate."]
    ids = [r["id"] for r in classes.get(ctx.con, s)["segments"]]
    assert all(set(p["seg"]) <= set(ids) for p in all_points(n) if not p.get("mine"))


def test_his_lines_kept_word_for_word(ctx):
    s = lesson(ctx)
    classes.save_notes(ctx.con, s, "[00:04] le chat = stress → shift\n[00:21] hw p112")
    run(class_notes.make_notes(ctx, s))
    pts = all_points(notes_of(ctx, s))
    mine = [p["text"] for p in pts if p.get("mine")]
    assert mine.count("le chat = stress → shift") == 1
    assert "hw p112" in mine                       # a line the model left out still shows up, as his
    assert "[00:21] hw p112" in "\n".join(ctx.runner.calls[0]["parts"])


def test_too_little_makes_no_call(ctx):
    s = classes.start(ctx.con, ctx.clock, "Chem", None, None)["id"]
    classes.add_segment(ctx.con, s, 0, 5, "room", "good morning everyone")
    classes.end(ctx.con, ctx.clock, s)
    assert run(class_notes.make_notes(ctx, s)) == "too_little"
    assert ctx.runner.calls == []


def bad(code="JSON_PARSE", usd=0.004, truncated=False):
    e = Retryable(code)
    e.cost_usd, e.truncated = usd, truncated
    return e


def test_bad_json_tries_twice_then_fails(ctx):
    """A reply that isn't JSON was paid for: one more try, never three (the Spanish class burned $0.68 this way)."""
    ctx.runner = FakeRunner(bad(), bad(), bad())
    s = lesson(ctx)
    assert run(class_notes.make_notes(ctx, s)) == "failed"
    assert len(ctx.runner.calls) == 2
    sess = classes.get(ctx.con, s)["session"]
    assert sess["status"] == "failed" and sess["cost"] == pytest.approx(0.008)


def test_network_blips_still_get_three_tries(ctx):
    ctx.runner = FakeRunner(Retryable("HTTP_503"), Retryable("HTTP_503"), NOTES)
    s = lesson(ctx)
    assert run(class_notes.make_notes(ctx, s)) == "noted"
    assert len(ctx.runner.calls) == 3


def test_cut_off_reply_retries_with_more_room(ctx):
    ctx.runner = FakeRunner(bad(truncated=True), NOTES)
    s = lesson(ctx)
    assert run(class_notes.make_notes(ctx, s)) == "noted"
    first, second = (c["max_tokens"] for c in ctx.runner.calls)
    assert second > first
    assert classes.get(ctx.con, s)["session"]["cost"] == pytest.approx(0.014)


def test_long_class_gets_room_to_answer(ctx):
    s = classes.start(ctx.con, ctx.clock, "Spanish", None, None)["id"]
    for i in range(509):
        classes.add_segment(ctx.con, s, i * 4, i * 4 + 3, "room", "hablamos de los restaurantes en Singapur hoy")
    classes.end(ctx.con, ctx.clock, s)
    run(class_notes.make_notes(ctx, s))
    assert ctx.runner.calls[0]["max_tokens"] >= 5000


def test_spend_cap_per_class(ctx):
    """Restarts and Regenerate can't keep paying for one class: past the cap, no more calls."""
    s = lesson(ctx)
    ctx.con.execute("UPDATE class_sessions SET cost=? WHERE id=?", (class_notes.CAP_USD, s))
    assert run(class_notes.make_notes(ctx, s)) == "failed"
    assert ctx.runner.calls == []


def test_due_dates_resolve_in_sgt():
    fri = date(2026, 10, 23)
    assert class_notes.resolve_due("next Tuesday", fri) == "2026-10-27"
    assert class_notes.resolve_due("tomorrow", fri) == "2026-10-24"
    assert class_notes.resolve_due("28 Oct", fri) == "2026-10-28"
    assert class_notes.resolve_due("Oct 28", fri) == "2026-10-28"
    assert class_notes.resolve_due("in 3 days", fri) == "2026-10-26"
    assert class_notes.resolve_due("Monday", fri) == "2026-10-26"
    assert class_notes.resolve_due("2026-11-02", fri) == "2026-11-02"
    assert class_notes.resolve_due("5 Jan", fri) == "2027-01-05"          # already past this year: next year
    assert class_notes.resolve_due("soon", fri) is None


def test_actions_saved_with_seg(ctx):
    s = lesson(ctx)
    run(class_notes.make_notes(ctx, s))
    acts = classes.get(ctx.con, s)["actions"]
    assert [(a["text"], a["kind"], a["due"], a["seg_id"]) for a in acts] == \
        [("Questions 1 to 8 on page 112", "homework", "2026-10-27", acts[0]["seg_id"])]
    assert acts[0]["seg_id"] == classes.get(ctx.con, s)["segments"][2]["id"]


def test_regenerate_replaces_notes_and_actions(ctx):
    s = lesson(ctx)
    run(class_notes.make_notes(ctx, s))
    other = json.loads(json.dumps(NOTES))
    other["summary"] = "Second go."
    other["action_items"][0]["text"] = "Questions 1 to 10"
    ctx.runner = FakeRunner(other)
    run(class_notes.make_notes(ctx, s))
    assert notes_of(ctx, s)["summary"] == "Second go."
    assert [a["text"] for a in classes.get(ctx.con, s)["actions"]] == ["Questions 1 to 10"]


def test_runs_while_background_work_paused(ctx):
    ctx.settings.set("paused", True)
    s = lesson(ctx)
    assert run(class_notes.make_notes(ctx, s)) == "noted"


def test_class_end_makes_the_notes(ctx):
    from misty_engine.api_class import class_methods
    sent = []

    async def notify(m, p):
        sent.append((m, p["status"]))
    ctx.notify = notify
    m = class_methods(ctx)

    async def go():
        s = (await m["class.start"]({"subject": "Chem"}))["id"]
        for i, t in enumerate(["Le Chatelier says a stress shifts it", "dynamic equilibrium means equal rates",
                               "homework questions one to eight page 112 next Tuesday"]):
            await m["class.segment"]({"session": s, "t_start": i * 10, "t_end": i * 10 + 9, "text": t + " x" * 60})
        await m["class.end"]({"session": s})
        for _ in range(50):
            await asyncio.sleep(0)
        return s
    s = run(go())
    assert classes.get(ctx.con, s)["session"]["status"] == "noted"
    assert sent == [("class.notes", "noting"), ("class.notes", "noted")]


def test_lost_and_next_step_only_keep_what_the_transcript_backs(ctx):
    reply = {**NOTES,
             "lost": [{"text": "Asked why a catalyst does not move the equilibrium", "seg": [2]},
                      {"text": "Seemed confused about Kc", "seg": [999]},
                      {"text": "", "seg": [1]}, "not a dict"],
             "next_step": {"text": "Do questions 1 to 8 on page 112", "seg": [3]}}
    ctx.runner = FakeRunner(reply)
    s = lesson(ctx)
    run(class_notes.make_notes(ctx, s))
    n = notes_of(ctx, s)
    assert n["lost"] == [{"text": "Asked why a catalyst does not move the equilibrium", "seg": [2]}]
    assert n["next_step"] == {"text": "Do questions 1 to 8 on page 112", "seg": [3]}


def test_next_step_dropped_when_not_backed_and_old_replies_still_work(ctx):
    ctx.runner = FakeRunner({**NOTES, "next_step": {"text": "Revise everything", "seg": [999]}})
    s = lesson(ctx)
    run(class_notes.make_notes(ctx, s))
    n = notes_of(ctx, s)
    assert n["next_step"] is None and n["lost"] == []      # no "lost" key in the reply at all


def test_a_line_he_typed_twice_is_kept_twice():
    his = ["hw p112", "hw p112", "bring calculator"]
    data = {"sections": [{"heading": "Homework", "points": [{"text": "hw p112", "mine": True}]}]}
    n = class_notes.check(data, {1}, his, date(2026, 10, 23), [])
    mine = [p["text"] for sec in n["sections"] for p in sec["points"] if p["mine"]]
    assert sorted(mine) == sorted(his)
    placed_twice = {"sections": [{"heading": "A", "points": [{"text": "hw p112", "mine": True}, {"text": "hw p112", "mine": True},
                                                            {"text": "hw p112", "mine": True}]}]}
    mine = [p["text"] for sec in class_notes.check(placed_twice, {1}, his, date(2026, 10, 23), [])["sections"]
            for p in sec["points"] if p["mine"]]
    assert sorted(mine) == sorted(his)                      # never more of his lines than he typed


def test_blank_points_remember_items_and_actions_are_dropped():
    blank = {"summary": "s", "sections": [{"heading": "A", "points": [{"text": "  ", "seg": [1]}]}],
             "remember": [{"kind": "formula", "text": "", "seg": [1]}],
             "action_items": [{"text": "   ", "kind": "homework", "due": None, "due_text": "", "seg": [1]}]}
    n = class_notes.check(blank, {1}, [], date(2026, 10, 23), [])
    assert n["sections"] == [] and n["remember"] == [] and n["action_items"] == []


def test_a_due_date_before_the_class_is_not_trusted():
    fri = date(2026, 10, 23)
    act = lambda **kw: {"action_items": [{"text": "Essay", "kind": "homework", "seg": [1], **kw}]}
    n = class_notes.check(act(due="2025-10-30", due_text="next Tuesday"), {1}, [], fri, [])
    assert n["action_items"][0]["due"] == "2026-10-27"            # the model's year was wrong; the teacher's words win
    assert class_notes.check(act(due="2025-10-30", due_text=""), {1}, [], fri, [])["action_items"][0]["due"] is None
    assert class_notes.check(act(due="2026-10-30", due_text=""), {1}, [], fri, [])["action_items"][0]["due"] == "2026-10-30"
    assert class_notes.check(act(due="2026-10-23", due_text=""), {1}, [], fri, [])["action_items"][0]["due"] == "2026-10-23"


def test_a_reply_that_is_not_an_object_still_costs_what_it_cost(ctx):
    ctx.runner = FakeRunner(["not", "an", "object"])
    s = lesson(ctx)
    assert run(class_notes.make_notes(ctx, s)) == "failed"
    assert classes.get(ctx.con, s)["session"]["cost"] == pytest.approx(0.01)


def test_notes_name_a_class_that_still_has_its_default_name(ctx):
    s = lesson(ctx)
    ctx.runner = FakeRunner({**NOTES, "title": "Le Chatelier's principle"})
    assert run(class_notes.make_notes(ctx, s)) == "noted"
    assert classes.get(ctx.con, s)["session"]["title"] == "Le Chatelier's principle, 23 Oct"


def test_notes_never_rename_a_class_he_named(ctx):
    s = lesson(ctx)
    classes.rename(ctx.con, s, "My chem class", None)
    ctx.runner = FakeRunner({**NOTES, "title": "Le Chatelier's principle"})
    assert run(class_notes.make_notes(ctx, s)) == "noted"
    assert classes.get(ctx.con, s)["session"]["title"] == "My chem class"


def test_calendar_title_names_the_class(ctx):
    from misty_engine.api_class import class_methods
    m = class_methods(ctx)
    out = run(m["class.start"]({"subject": "Physics", "cal_title": "IB Physics HL"}))
    assert out["title"] == "IB Physics HL, 23 Oct"
    out = run(m["class.rename"]({"session": out["id"], "subject": "Math"}))
    assert out["subject"] == "Math" and out["title"] == "IB Physics HL, 23 Oct"
