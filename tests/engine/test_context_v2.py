import json
import re

import pytest

from misty_engine.registry import render
from misty_engine.registry.store import Registry
from misty_engine.wiki.context import build_context
from test_render_lint import world  # noqa: F401


@pytest.fixture
def big(world):
    """A registry wiki: 12 notes, 6 entities with pages, one merged pair (Pranav), one private chat."""
    ctx = world.ctx
    ids = []
    for i in range(12):
        iid, _ = world.item(f"it_bg{i:012d}", dated=[(f"2026-10-{1 + i:02d}", f"iGEM wiki step {i}")],
                            summary=f"iGEM wiki progress note number {i}. More detail follows here.")
        ids.append(iid)
    world.ids = ids
    world.igem = world.entity("project", "iGEM wiki", ids, summary="The team wiki for the iGEM competition.",
                              path="projects/igem-wiki.md", now=["Write part 3 docs"])
    world.aarav = world.entity("person", "Aarav Shah", ids[:6], summary="Teammate who runs the wiki sync.",
                               path="people/aarav-shah.md")
    world.pranav = world.entity("person", "Pranav Asrani", ids[3:8], summary="Friend from school.",
                                path="people/pranav-asrani.md")
    dup = world.entity("person", "Pranav", ids[8:10], summary="Same Pranav.", path="people/pranav.md")
    for e in (world.igem, world.aarav, world.pranav, dup):
        render.write_entity(ctx, e)
    Registry(ctx.con, ctx.clock).merge(world.pranav, dup, reason="R7")
    render.drop_page(ctx, dup)
    render.write_entity(ctx, world.pranav)
    ctx.con.execute("UPDATE entities SET importance=4")
    return world


def test_one_entry_per_entity_even_after_a_merge(big):
    pack = build_context(big.ctx, "Pranav", 2000)
    assert len({e.id for e in pack.entities}) == len(pack.entities)
    assert sum(1 for e in pack.entities if "pranav" in e.name.lower()) == 1
    assert pack.entities[0].id == big.pranav and pack.entities[0].path == "people/pranav-asrani.md"


@pytest.mark.parametrize("budget", [60, 200, 500, 1000, 3000])
def test_hard_budget(big, budget):
    assert build_context(big.ctx, "iGEM wiki", budget).tokens <= budget


def test_deterministic(big):
    a = build_context(big.ctx, "iGEM wiki", 1500)
    b = build_context(big.ctx, "iGEM wiki", 1500)
    assert a.text == b.text and [e.id for e in a.entities] == [e.id for e in b.entities]


def test_every_entity_carries_freshness_and_facts(big):
    pack = build_context(big.ctx, "iGEM wiki", 3000)
    e = pack.entities[0]
    assert e.freshness_days is not None and e.freshness_days >= 0
    assert "last_interaction" in e.facts and e.facts["open_items"] == 0 and e.why


def test_dropped_reported_when_the_budget_cuts_entities(big):
    pack = build_context(big.ctx, "iGEM wiki Aarav Pranav", 90)
    assert pack.dropped and all(d["reason"] == "budget" for d in pack.dropped)
    full = build_context(big.ctx, "iGEM wiki Aarav Pranav", 3000)
    assert not full.dropped


def test_about_card_is_cut_before_an_entity_is(big):
    big.ctx.wiki.write_text("about-isaac.md", "---\ntype: about\ntitle: About\nupdated: x\n---\n\n" + "A fact. " * 400)
    pack = build_context(big.ctx, "iGEM wiki", 120)
    assert pack.tokens <= 120 and pack.entities                          # the entity stays, the card gave way
    assert "about" not in pack.sources_used or pack.text.index("## [[") > 0


def test_private_excluded_unless_asked(world):
    ctx = world.ctx
    pub, _ = world.item("it_pv00000000a1b1", dated=[("2026-10-10", "public line")], chat_id="c1", source="whatsapp")
    pub2, _ = world.item("it_pv00000000a2b2", dated=[("2026-10-11", "public line two")], chat_id="c1",
                         source="whatsapp")
    prv, _ = world.item("it_pv00000000a3c3", dated=[("2026-10-12", "SECRET-LINE hidden")], chat_id="cp",
                        source="whatsapp")
    e = world.entity("person", "Aarav Shah", [pub, pub2, prv], summary="Teammate.", path="people/aarav-shah.md")
    render.write_entity(ctx, e)
    ctx.settings.set("context_private_chats", ["whatsapp:cp"])
    assert "SECRET-LINE" not in build_context(ctx, "Aarav", 2000).text
    pack = build_context(ctx, "Aarav", 2000, include_private=True)
    assert "SECRET-LINE" in pack.text


def test_named_entity_is_included_even_if_the_topic_misses_it(big):
    pack = build_context(big.ctx, "zzzz unrelated words", 2000, entities=[big.aarav])
    assert pack.entities[0].id == big.aarav and pack.entities[0].why == "named in the request"
    assert all(e.why == "shares notes with a matched entity" for e in pack.entities[1:])


def test_open_now_has_cards_dates_and_now_bullets(big):
    ctx = big.ctx
    from misty_engine import suggest
    suggest.add(ctx.con, big.ids[0], "reply", "Reply to Aarav: confirm the sync", "today", None, ctx.clock)
    ctx.con.execute("INSERT INTO items(id, source, kind, locator, title, status, note_path, meta, modified_at, "
                    "updated_at) VALUES ('it_soon0000000001','gmail','mail_thread','x','t','done',"
                    "'sources/gmail/soon01.md',?, '2026-10-15T00:00:00+00:00', 0)",
                    (json.dumps({"dated_lines": [["2026-10-18", "Demo day for the iGEM wiki"]]}),))
    (ctx.paths.wiki / "sources/gmail").mkdir(parents=True, exist_ok=True)
    (ctx.paths.wiki / "sources/gmail/soon01.md").write_text("---\ntype: source\ntitle: t\nitem_id: x\nsource: gmail\n"
                                                              "updated: 2026-10-14\n---\n\n## Summary\ns\n")
    Registry(ctx.con, ctx.clock).add_mention("it_soon0000000001", big.igem, "iGEM wiki")
    render.write_entity(ctx, big.igem)
    pack = build_context(ctx, "iGEM wiki", 3000)
    kinds = {o["kind"] for o in pack.open_now}
    assert {"card", "date", "now"} <= kinds and len(pack.open_now) <= 8
    assert "## open now" in pack.text


def test_unmigrated_pages_still_work_as_one_pseudo_entity_each(fctx):
    from test_context_pack import put
    put(fctx, "projects/a", "project", "Alpha topic", "alpha alpha", importance=4)
    pack = build_context(fctx, "Alpha topic alpha", 2000)
    assert [e.path for e in pack.entities] == ["projects/a.md"] and pack.entities[0].id == "projects/a.md"


def test_pack_duplicate_count_is_zero_after_a_merge(big):
    from misty_engine import quality
    assert quality.pack_duplicate_count(big.ctx, ["Pranav", "iGEM wiki", "Aarav"]) == 0


def test_open_now_only_comes_from_entities_that_are_in_the_pack(big):
    ctx = big.ctx
    for eid, bullet in ((big.aarav, "Send Aarav the sync notes"), (big.pranav, "Return Pranav's charger")):
        ctx.con.execute("UPDATE entity_text SET now=? WHERE entity_id=?", (json.dumps([bullet]), eid))
    saw_a_cut = False
    for budget in (90, 130, 180, 260, 400, 3000):
        pack = build_context(ctx, "iGEM wiki Aarav Pranav", budget)
        shown = {e.name for e in pack.entities}
        saw_a_cut = saw_a_cut or bool(pack.dropped)
        for o in pack.open_now:
            if o["kind"] in ("now", "date"):
                assert o["text"].split(":")[0] in shown, (budget, o, shown)
        for name in ("Aarav Shah", "Pranav Asrani"):                 # the text and the list say the same thing
            assert (name in shown) or (name not in pack.text), (budget, name)
    assert saw_a_cut                                                 # the test really did cut entities


def test_page_header_shows_last_activity_not_the_render_date(big):
    pack = build_context(big.ctx, "iGEM wiki", 3000)
    e = pack.entities[0]
    last = e.facts["last_interaction"][:10]
    assert f"({e.type} · last activity {last})" in pack.text
    assert "· updated" not in pack.text
