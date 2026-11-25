"""Class action items on Priorities: their own section the weekly flow never touches, due/class/done kept through every
rewrite, ticking syncs both ways, and old lines clear out a week later."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from misty_engine import class_notes, classes
from misty_engine.api_class import class_methods
from misty_engine.runner import RunResult
from misty_engine.wiki import priorities as pr
from misty_engine.wiki.store import WikiStore

DAY = 86400.0
FRI = 1_792_720_800.0     # 2026-10-23 10:00 SGT


@pytest.fixture
def store(misty_home, clock):
    clock.t = FRI
    w = WikiStore(misty_home.wiki, clock)
    w.init()
    return w


ITEMS = [{"id": "ca_1", "text": "Questions 1 to 8 on page 112", "subject": "Chem", "due": "2026-10-27",
          "session": "cs_aa"}]


def test_class_lines_survive_weekly_apply(store, clock):
    pr.write_draft(store, [{"text": "Ship iGEM wiki"}], clock)
    pid = pr.add_class_items(store, clock, ITEMS)[0]
    pr.apply(store, clock)
    store.write_text(pr.PROPOSED, (store.root / pr.PATH).read_text().replace("Ship iGEM wiki", "NOI prep"))
    pr.apply_proposal(store, clock, [d.id for d in pr.diff(pr._lines(store), pr.proposed(store))])
    got = pr.get(store)
    assert [l["text"] for l in got["lines"]] == ["NOI prep"]
    assert [(c["id"], c["text"], c["due"]) for c in got["from_class"]] == \
        [(pid, "Chem: Questions 1 to 8 on page 112", "2026-10-27")]


def test_fields_round_trip_render(store, clock):
    pid = pr.add_class_items(store, clock, ITEMS)[0]
    pr.write_draft(store, [{"text": "A"}], clock)
    c = pr.get(store)["from_class"][0]
    assert c == {"id": pid, "text": "Chem: Questions 1 to 8 on page 112", "area": "chem", "due": "2026-10-27",
                 "cls": "cs_aa", "done": None}
    pr.set_done(store, clock, pid, True)
    pr.apply(store, clock)
    assert pr.get(store)["from_class"][0]["done"] == "2026-10-23"
    assert "- [x] Chem: Questions" in (store.root / pr.PATH).read_text()


def test_old_done_lines_cleared_after_7_days(store, clock):
    a, b = pr.add_class_items(store, clock, ITEMS + [{"id": "ca_2", "text": "Read 4.2", "subject": "Physics",
                                                        "due": "2026-10-24", "session": "cs_bb"}])
    pr.set_done(store, clock, a, True)
    clock.advance(6 * DAY)                      # 2026-10-29: done 6 days ago, b 5 days overdue
    pr.apply(store, clock)
    assert {c["id"] for c in pr.get(store)["from_class"]} == {a, b}
    clock.advance(3 * DAY)                      # 2026-11-01: done 9 days ago, b 8 days overdue
    pr.apply(store, clock)
    assert pr.get(store)["from_class"] == []


class FakeRunner:
    async def run(self, system, parts, schema_name, **kw):
        r = {"summary": "s", "sections": [], "remember": [],
             "action_items": [{"text": "Questions 1 to 8 on page 112", "kind": "homework", "due": "2026-10-27",
                               "due_text": "next Tuesday", "seg": [1]}]}
        return RunResult(text=json.dumps(r), json=r, cost_usd=0.0)


def _noted_class(con, clock, store, settings):
    ctx = SimpleNamespace(con=con, clock=clock, settings=settings, runner=FakeRunner(), wiki=store, notify=None)
    s = classes.start(con, clock, "Chem", None, None)["id"]
    classes.add_segment(con, s, 0, 10, "room", "homework page 112 " + "words " * 200)
    classes.end(con, clock, s)
    asyncio.run(class_notes.make_notes(ctx, s))
    return ctx, s


def test_notes_add_priority_lines(con, clock, store, settings):
    ctx, s = _noted_class(con, clock, store, settings)
    act = classes.get(con, s)["actions"][0]
    assert act["priority_id"]
    assert pr.get(store)["from_class"][0]["id"] == act["priority_id"]
    assert pr.get(store)["from_class"][0]["cls"] == s


def test_done_syncs_both_ways(con, clock, store, settings):
    ctx, s = _noted_class(con, clock, store, settings)
    m = class_methods(ctx)
    act = classes.get(con, s)["actions"][0]
    asyncio.run(m["class.action.done"]({"action": act["id"], "done": True}))
    assert pr.get(store)["from_class"][0]["done"] == "2026-10-23"
    asyncio.run(m["priorities.done"]({"id": act["priority_id"], "done": False}))
    assert classes.get(con, s)["actions"][0]["done"] == 0
    assert pr.get(store)["from_class"][0]["done"] is None


def test_delete_class_removes_its_priority_lines(con, clock, store, settings):
    ctx, s = _noted_class(con, clock, store, settings)
    pr.add_class_items(store, clock, [{"id": "x", "text": "Other class item", "subject": "Math", "due": None,
                                       "session": "cs_other"}])
    asyncio.run(class_methods(ctx)["class.delete"]({"session": s}))
    assert [c["cls"] for c in pr.get(store)["from_class"]] == ["cs_other"]


def test_regenerate_with_no_action_items_clears_the_old_priority_lines(con, clock, store, settings):
    ctx, s = _noted_class(con, clock, store, settings)
    assert len(pr.get(store)["from_class"]) == 1
    pr.add_class_items(store, clock, [{"id": "x", "text": "Other class item", "subject": "Math", "due": None,
                                       "session": "cs_other"}])
    class NoActions(FakeRunner):
        async def run(self, system, parts, schema_name, **kw):
            r = {"summary": "s", "sections": [], "remember": [], "action_items": []}
            return RunResult(text=json.dumps(r), json=r, cost_usd=0.0)
    ctx.runner = NoActions()
    asyncio.run(class_notes.make_notes(ctx, s))
    assert classes.get(con, s)["actions"] == []
    assert [c["cls"] for c in pr.get(store)["from_class"]] == ["cs_other"]        # this class's old line is gone


def test_merge_gives_one_class_one_set_of_notes_and_priority_lines(con, clock, store, settings):
    ctx, a = _noted_class(con, clock, store, settings)
    clock.advance(1800)
    class Second(FakeRunner):
        async def run(self, system, parts, schema_name, **kw):
            r = {"summary": "s", "sections": [], "remember": [],
                 "action_items": [{"text": "Do the atom economy sheet", "kind": "homework", "due": None,
                                   "due_text": "", "seg": [2]}]}
            return RunResult(text=json.dumps(r), json=r, cost_usd=0.0)
    ctx.runner = Second()
    s2 = classes.start(con, clock, "Chem", "Atom economy", None)["id"]
    classes.add_segment(con, s2, 0, 10, "room", "second half " + "words " * 200)
    classes.end(con, clock, s2)
    asyncio.run(class_notes.make_notes(ctx, s2))
    assert {c["cls"] for c in pr.get(store)["from_class"]} == {a, s2}
    wiki_page = (store.root / "classes" / "chem.md")
    assert wiki_page.read_text().count("\n## ") == 2

    async def go():
        out = await class_methods(ctx)["class.merge"]({"sessions": [s2, a]})
        await asyncio.sleep(0.2)                                   # the notes job the merge started
        return out
    ctx.runner = FakeRunner()
    out = asyncio.run(go())
    assert (out["kept"], out["absorbed"]) == (a, s2)
    assert [c["id"] for c in classes.list(con)] == [a]
    assert classes.get(con, a)["session"]["status"] == "noted"     # notes made again over the whole lesson
    assert len(classes.get(con, a)["segments"]) == 2
    assert {c["cls"] for c in pr.get(store)["from_class"]} == {a}  # the other half's lines are gone, this one's are back
    assert wiki_page.read_text().count("\n## ") == 1
    import pytest
    from misty_engine.errors import Fatal
    with pytest.raises(Fatal):
        asyncio.run(class_methods(ctx)["class.merge"]({"sessions": [a]}))
