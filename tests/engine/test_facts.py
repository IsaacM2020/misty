import json

from misty_engine import suggest
from misty_engine.registry import facts, render
from misty_engine.registry.store import Registry
from test_render_lint import world  # noqa: F401


def test_last_interaction_is_the_newest_past_date_and_next_date_the_nearest_future(world):
    ctx = world.ctx                                     # clock = 2026-10-15 SGT
    a, _ = world.item("it_fa0000000000a1", dated=[("2026-10-01", "met"), ("2026-10-12", "called"),
                                                  ("2026-10-20", "demo day")], modified="2026-10-12T00:00:00+00:00")
    b, _ = world.item("it_fa0000000000b2", dated=[("2026-11-03", "exam")], modified="2026-10-13T00:00:00+00:00")
    e = world.entity("person", "Aarav Shah", [a, b])
    f = facts.compute(ctx, e)
    assert f["last_interaction"] == "2026-10-13" and f["first_seen"] == "2026-10-01"
    assert f["next_date"] == "2026-10-20"
    assert f["as_of"] == "2026-10-15"


def test_future_line_is_never_the_last_interaction(world):
    ctx = world.ctx
    a, _ = world.item("it_fb0000000000a1", dated=[("2026-12-25", "party")], modified="2026-12-24T00:00:00+00:00")
    e = world.entity("person", "Aarav Shah", [a])
    f = facts.compute(ctx, e)
    assert f["last_interaction"] is None and f["next_date"] == "2026-12-25"


def test_open_items_are_pending_suggestions_only(world):
    ctx = world.ctx
    a, _ = world.item("it_fc0000000000a1")
    b, _ = world.item("it_fc0000000000b2")
    e = world.entity("person", "Aarav Shah", [a, b])
    s1 = suggest.add(ctx.con, a, "reply", "Reply to Aarav: confirm the sync", "today", None, ctx.clock)
    s2 = suggest.add(ctx.con, b, "task", "Send the part 2 docs", "this_week", None, ctx.clock)
    ctx.con.execute("UPDATE suggestions SET status='dismissed' WHERE id=?", (s2,))
    items = facts.open_items(ctx, e)
    assert [i["item_id"] for i in items] == [a] and items[0]["kind"] == "reply" and "confirm the sync" in items[0]["text"]
    assert facts.compute(ctx, e)["open_items"] == 1


def test_private_notes_contribute_nothing(world):
    ctx = world.ctx
    pub, _ = world.item("it_fd0000000000a1", dated=[("2026-10-05", "public")], chat_id="c1", source="whatsapp")
    prv, _ = world.item("it_fd0000000000b2", dated=[("2026-10-13", "secret")], chat_id="cp", source="whatsapp",
                        modified="2026-10-13T00:00:00+00:00")
    e = world.entity("person", "Aarav Shah", [pub, prv])
    suggest.add(ctx.con, prv, "reply", "Private thing", "now", None, ctx.clock)
    ctx.settings.set("context_private_chats", ["whatsapp:cp"])
    f = facts.compute(ctx, e)
    assert f["last_interaction"] == "2026-10-12" and f["open_items"] == 0       # the public note's modified date
    assert facts.open_items(ctx, e) == []


def test_collaborators_for_projects_are_people_in_two_or_more_notes(world):
    ctx = world.ctx
    a, _ = world.item("it_fe0000000000a1")
    b, _ = world.item("it_fe0000000000b2")
    c, _ = world.item("it_fe0000000000c3")
    proj = world.entity("project", "iGEM wiki", [a, b, c])
    world.entity("person", "Aarav Shah", [a, b])
    world.entity("person", "Priya Rao", [a])                                     # only one shared note
    f = facts.compute(ctx, proj)
    assert f["collaborators"] == ["Aarav Shah"]
    assert "collaborators" not in facts.compute(ctx, world.entity("person", "Zed Roe", [a, b]))


def test_handles_come_from_the_registry_and_never_into_frontmatter(world):
    ctx = world.ctx
    a, _ = world.item("it_ff0000000000a1")
    b, _ = world.item("it_ff0000000000b2")
    e = world.entity("person", "Aarav Shah", [a, b])
    Registry(ctx.con, ctx.clock).add_handle(e, "phone", "+6591234567")
    assert facts.handles(ctx, e) == [{"type": "phone", "value": "+6591234567"}]
    fm, body = render.render_entity(ctx, e)
    assert "+6591234567" not in json.dumps(fm) and "+6591234567" not in body
    assert fm["facts"]["open_items"] == 0 and "as_of" in fm["facts"]
