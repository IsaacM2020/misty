import json

import pytest

from misty_engine.registry import render
from misty_engine.registry.store import Registry
from misty_engine.wiki.notes import section, validate_page

NOTE = ("---\ntype: source\ntitle: {t}\nitem_id: {i}\nsource: {s}\nupdated: 2026-10-14\n---\n\n"
        "## Summary\n{sm}\n\n## Key facts\n- f\n\n## People\n- (none)\n\n## Links\n- (none)\n")


@pytest.fixture
def world(ctx_runner):
    ctx, _ = ctx_runner()
    reg = Registry(ctx.con, ctx.clock)

    class W:
        pass
    w = W()
    w.ctx, w.reg = ctx, reg

    def item(iid, *, title="Wiki sync thread", summary="We agreed to sync the wiki weekly. More text.",
             modified="2026-10-12T00:00:00+00:00", dated=(), people=(), projects=(), topics=(), source="gmail",
             chat_id=None):
        meta = {"people": list(people), "projects": list(projects), "topics": list(topics),
                "dated_lines": [list(d) for d in dated]}
        if chat_id:
            meta["chat_id"] = chat_id
        path = f"sources/{source}/{iid[-6:]}.md"
        (ctx.paths.wiki / path).parent.mkdir(parents=True, exist_ok=True)
        (ctx.paths.wiki / path).write_text(NOTE.format(t=title, i=iid, s=source, sm=summary))
        ctx.con.execute("INSERT OR REPLACE INTO items(id, source, kind, locator, title, status, note_path, "
                        "modified_at, meta, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (iid, source, "mail_thread", f"/x/{iid}", title, "done", path, modified,
                         json.dumps(meta), ctx.clock.now()))
        return iid, path

    def entity(kind, name, items, *, summary="A real summary.", status="active", path=None, now=None, raws=None):
        eid = reg.new_entity(kind, name, status=status, path=path)
        for iid in items:
            reg.add_mention(iid, eid, (raws or {}).get(iid, name))
        if summary is not None:
            ctx.con.execute("INSERT INTO entity_text(entity_id, summary, now, summary_n) VALUES (?,?,?,?)",
                            (eid, summary, json.dumps(now or []), len(items)))
        return eid

    w.item, w.entity = item, entity
    return w


def test_page_needs_summary(world):
    i, _ = world.item("it_000000000000a1")
    e = world.entity("person", "Aarav Shah", [i], summary=None)
    assert render.render_entity(world.ctx, e) is None
    e2 = world.entity("person", "Priya Rao", [i], summary="(stub)")
    assert render.render_entity(world.ctx, e2) is None


def test_candidate_has_no_page_even_with_summary(world):
    i, _ = world.item("it_000000000000a1")
    e = world.entity("person", "Aarav Shah", [i], status="candidate")
    assert render.render_entity(world.ctx, e) is None and render.write_entity(world.ctx, e) is None


def test_timeline_never_uses_item_title(world):
    i, _ = world.item("it_000000000000a1", title="Wiki sync thread")
    e = world.entity("topic", "Wiki", [i])
    fm, body = render.render_entity(world.ctx, e)
    assert "Wiki sync thread" not in body
    assert "- 2026-10-12: We agreed to sync the wiki weekly." in body


def test_private_lines_excluded(world):
    pub, _ = world.item("it_000000000000a1", dated=[("2026-10-10", "PUBLIC-LINE")], chat_id="c_pub", source="whatsapp")
    prv, _ = world.item("it_000000000000a2", dated=[("2026-10-11", "SECRET-LINE")], chat_id="c_prv", source="whatsapp")
    e = world.entity("person", "Aarav Shah", [pub, prv])
    world.ctx.settings.set("context_private_chats", ["whatsapp:c_prv"])
    fm, body = render.render_entity(world.ctx, e)
    assert "SECRET-LINE" not in body and "PUBLIC-LINE" in body
    assert "it_000000000000a2" not in fm["sources"] and fm["sources"] == ["it_000000000000a1"]


def test_private_only_entity_has_no_page(world):
    prv, _ = world.item("it_000000000000a2", chat_id="c_prv", source="whatsapp")
    e = world.entity("person", "Aarav Shah", [prv])
    world.ctx.settings.set("context_private_chats", ["whatsapp:c_prv"])
    assert render.render_entity(world.ctx, e) is None


def test_timeline_dedupes_newest_first_and_caps(world):
    ids = []
    for n in range(3):
        i, _ = world.item(f"it_00000000000b{n:02d}", dated=[("2026-10-01", "Same thing happened"),
                                                             (f"2026-10-0{n + 2}", f"Event {n}")])
        ids.append(i)
    e = world.entity("topic", "Wiki", ids)
    tl = section(render.render_entity(world.ctx, e)[1], "Timeline").splitlines()
    dates = [l[2:12] for l in tl]
    assert dates == sorted(dates, reverse=True) and sum("Same thing" in l for l in tl) == 1
    many, _ = world.item("it_00000000000c00", dated=[(f"2026-{1 + n // 28:02d}-{1 + n % 28:02d}", f"E{n}") for n in range(120)])
    e2 = world.entity("topic", "Busy", [many])
    assert len(section(render.render_entity(world.ctx, e2)[1], "Timeline").splitlines()) == 80


def test_person_gets_only_lines_naming_them_in_crowded_notes(world):
    i, _ = world.item("it_000000000000a1", people=["Aarav Shah", "Priya", "Ken"],
                      dated=[("2026-10-20", "Aarav Shah presents results"), ("2026-10-21", "Lab cleaning")])
    e = world.entity("person", "Aarav Shah", [i])
    tl = section(render.render_entity(world.ctx, e)[1], "Timeline")
    assert "presents results" in tl and "Lab cleaning" not in tl


def test_links_are_top_co_mentioned_active_pages(world):
    i1, _ = world.item("it_000000000000a1")
    i2, _ = world.item("it_000000000000a2")
    a = world.entity("person", "Aarav Shah", [i1, i2], path="people/aarav-shah.md")
    world.entity("project", "iGEM Wiki", [i1, i2], path="projects/igem-wiki.md")
    world.entity("topic", "Ghost", [i1], status="candidate")
    world.entity("topic", "Exams", [i1], path="topics/exams.md")
    links = section(render.render_entity(world.ctx, a)[1], "Links").splitlines()
    assert links == ["- [[projects/igem-wiki]]", "- [[topics/exams]]"]


def test_no_dead_links_after_relink(world):
    i, note = world.item("it_000000000000a1", people=["Active One", "Missing Two", "+65 9123 4567"],
                         summary="s.")
    a = world.entity("person", "Active One", [i], path="people/active-one.md")
    m = world.reg.new_entity("person", "Missing Two", status="candidate")
    world.reg.add_mention(i, m, "Missing Two")
    (world.ctx.paths.wiki / "people").mkdir(exist_ok=True)
    world.ctx.wiki.write("people/active-one.md", {"type": "person", "title": "Active One", "updated": "2026-10-12"},
                         "## Summary\nx\n\n## Now\n\n## Timeline\n\n## Links\n")
    assert render.relink_note(world.ctx, note, i) is True
    body = world.ctx.wiki.read(note).body
    assert "[[people/active-one]]" in body and "[[people/missing-two]]" not in body and "- Missing Two" in body
    assert "9123" not in body                                   # a phone number is not a person
    assert render.relink_note(world.ctx, note, i) is False       # idempotent


def test_lint_clean_after_render_all(world):
    i1, _ = world.item("it_000000000000a1", people=["Aarav Shah"], projects=["iGEM Wiki"])
    i2, _ = world.item("it_000000000000a2", people=["Aarav Shah"], projects=["iGEM Wiki"])
    es = [world.entity("person", "Aarav Shah", [i1, i2]), world.entity("project", "iGEM Wiki", [i1, i2])]
    for e in es:
        assert render.write_entity(world.ctx, e)
    assert all(v == 0 for v in render.lint(world.ctx).values()), render.lint(world.ctx)


def test_lint_counts_each_invariant(world):
    ctx = world.ctx
    i1, n1 = world.item("it_000000000000a1")
    i2, _ = world.item("it_000000000000a2")
    # a legacy stub with 2 sources, a dead link, an alias collision, a one-source page, a chat-name person
    ctx.wiki.write("people/legacy.md", {"type": "person", "title": "Legacy", "updated": "2026-10-12",
                                        "sources": [i1, i2]},
                   "## Summary\n(stub)\n\n## Now\n\n## Timeline\n\n## Links\n- [[people/ghost]]\n")
    a = world.entity("person", "Rahul Mehta", [i1])
    b = world.entity("person", "Rahul Mehta", [i2])
    world.entity("person", "MC SA2 OVER chat member", [i1, i2])
    r = render.lint(ctx)
    assert r["dead_links"] == 1 and r["stub_pages"] >= 1 and r["unsummarised_2plus"] >= 1
    assert r["orphan_files"] == 1 and r["multi_token_alias_collisions"] == 1
    assert r["few_sources"] >= 2 and r["chat_name_people"] == 1


def test_repair_drops_provable_orphans_and_rerenders_missing_pages(world):
    ctx = world.ctx
    i1, _ = world.item("it_000000000000a1")
    i2, _ = world.item("it_000000000000a2")
    e = world.entity("person", "Aarav Shah", [i1, i2])
    path = render.write_entity(ctx, e)
    ctx.wiki.write("people/gone.md", {"type": "person", "title": "Gone", "entity_id": "en_deadbeef0000",
                                      "updated": "2026-10-12"}, "## Summary\nx\n\n## Now\n\n## Timeline\n\n## Links\n")
    (ctx.paths.wiki / path).unlink()                              # a missing file for an active entity
    fixed = render.repair(ctx)
    assert fixed["pages_dropped"] == 1 and fixed["pages_rendered"] == 1 and fixed["legacy_pages"] == 0
    assert not ctx.wiki.exists("people/gone.md") and ctx.wiki.exists(path)
    assert render.repair(ctx) == {"pages_dropped": 0, "pages_rendered": 0, "notes_relinked": 0, "legacy_pages": 0}


def test_repair_leaves_an_unmigrated_wiki_alone(world):
    """Pages without an entity_id are pre-registry pages that notes still link to: nothing is relinked or deleted."""
    ctx = world.ctx
    i1, note = world.item("it_000000000000a1", people=["Legacy Person"])
    ctx.wiki.write("people/legacy-person.md", {"type": "person", "title": "Legacy Person", "updated": "2026-10-12"},
                   "## Summary\nx\n\n## Now\n\n## Timeline\n\n## Links\n")
    ctx.wiki.write("people/gone.md", {"type": "person", "title": "Gone", "entity_id": "en_deadbeef0000",
                                      "updated": "2026-10-12"}, "## Summary\nx\n\n## Now\n\n## Timeline\n\n## Links\n")
    before = ctx.wiki.head()
    fixed = render.repair(ctx)
    assert fixed["legacy_pages"] == 1 and fixed["pages_dropped"] == 0 and ctx.wiki.head() == before
    assert ctx.wiki.exists("people/legacy-person.md") and ctx.wiki.exists("people/gone.md")


def test_new_path_suffixes_on_collision(world):
    reg = world.reg
    reg.new_entity("person", "Sam Lee", status="active", path="people/sam-lee.md")
    assert render.new_path(reg, "person", "Sam Lee") == "people/sam-lee-2.md"
    reg.new_entity("person", "Sam Lee", status="active", path="people/sam-lee-2.md")
    assert render.new_path(reg, "person", "Sam Lee") == "people/sam-lee-3.md"


def test_redirected_path_is_not_reused(world):
    reg = world.reg
    a = reg.new_entity("person", "Sam Lee", status="active", path="people/sam-lee.md")
    b = reg.new_entity("person", "Sam", status="active", path="people/sam.md")
    reg.merge(a, b, reason="R4")
    assert render.new_path(reg, "person", "Sam") == "people/sam-2.md"


def test_page_roundtrips_validate(world):
    i1, _ = world.item("it_000000000000a1")
    e = world.entity("person", "Aarav Shah", [i1], now=["Presenting on Friday"])
    path = render.write_entity(world.ctx, e)
    p = world.ctx.wiki.read(path)
    assert validate_page(p.fm, p.body) == [] and p.fm["entity_id"] == e
    assert p.fm["sensitivity"] == "normal" and p.fm["summary_n"] == 1
    assert "- Presenting on Friday" in section(p.body, "Now")
    assert world.ctx.con.execute("SELECT path FROM entities WHERE id=?", (e,)).fetchone()[0] == path


def test_write_is_stable_no_new_commit_when_unchanged(world):
    i1, _ = world.item("it_000000000000a1")
    e = world.entity("person", "Aarav Shah", [i1])
    render.write_entity(world.ctx, e)
    head = world.ctx.wiki.head()
    world.ctx.clock.advance(3 * 86400)                            # a new day must not rewrite an unchanged page
    render.write_entity(world.ctx, e)
    assert world.ctx.wiki.head() == head


async def test_health_repairs_deterministically_and_reports_lint(world):
    from misty_engine.types import Job
    from misty_engine.wiki import health
    ctx = world.ctx
    i1, _ = world.item("it_000000000000a1")
    i2, _ = world.item("it_000000000000a2")
    e = world.entity("person", "Aarav Shah", [i1, i2])
    path = render.write_entity(ctx, e)
    (ctx.paths.wiki / path).unlink()
    await health.health_handler(Job("j1", "health", None, {}, False, "running", 0, 0.0, None), ctx)
    assert ctx.wiki.exists(path)                                    # re-rendered, no model involved
    s = ctx.settings.get("health_summary")
    assert s["lint"]["missing_files"] == 0 and s["lint"]["dead_links"] == 0
    assert "stub_pages" in (ctx.paths.wiki / "health.md").read_text()


def test_adopt_legacy_pages_migrates_an_unmigrated_wiki(world):
    """After `registry rebuild` the wiki still holds pre-registry pages. Adopting them stamps the ones that are an
    active entity's own page, removes the redirected and orphaned ones (old addresses keep resolving through
    entity_redirects), points the notes at the survivors and leaves a wiki that `repair` and `lint` call clean."""
    ctx, reg = world.ctx, world.reg
    i1, note = world.item("it_000000000000a1", people=["Aarav Shah", "Old Aarav"])
    i2, _ = world.item("it_000000000000a2", people=["Aarav Shah"])
    legacy_fm = {"type": "person", "updated": "2026-10-12"}
    body = "## Summary\nKept summary.\n\n## Now\n\n## Timeline\n\n## Links\n"
    # 1. a legacy page that IS an active entity's page (kept summary, no entity_id on disk)
    kept = world.entity("person", "Priya Rao", [i1, i2], path="people/priya-rao.md", summary="Kept summary.")   # as import_existing leaves it
    ctx.wiki.write("people/priya-rao.md", {**legacy_fm, "title": "Priya Rao"}, body)
    # 2. a legacy page whose old address was merged into a survivor that already has its own stamped page
    surv = world.entity("person", "Aarav Shah", [i1, i2], raws={i1: "Old Aarav"})
    surv_path = render.write_entity(ctx, surv)
    ctx.con.execute("INSERT INTO entity_redirects(old_path, entity_id) VALUES (?,?)", ("people/old-aarav.md", surv))
    ctx.wiki.write("people/old-aarav.md", {**legacy_fm, "title": "Old Aarav"}, body)
    # 3. a legacy page with no entity at all (the importer rejected the name)
    ctx.wiki.write("people/mc-sa2-chat-member.md", {**legacy_fm, "title": "MC SA2 chat member"}, body)
    assert render.legacy_pages(ctx) == 3

    out = render.adopt_legacy_pages(ctx)

    assert out["stamped"] == 1 and out["removed"] == 2 and out["legacy_pages"] == 0
    stamped = ctx.wiki.read("people/priya-rao.md")
    assert stamped.fm["entity_id"] == kept and "Kept summary." in stamped.body
    assert not ctx.wiki.exists("people/old-aarav.md") and not ctx.wiki.exists("people/mc-sa2-chat-member.md")
    assert ctx.wiki.exists(surv_path)
    assert render.legacy_pages(ctx) == 0
    lint = render.lint(ctx)
    assert lint["orphan_files"] == 0 and lint["dead_links"] == 0, lint
    assert f"[[{surv_path[:-3]}]]" in ctx.wiki.read(note).body        # the note links to the survivor, not the old page
    assert render.adopt_legacy_pages(ctx) == {"stamped": 0, "removed": 0, "demoted": 0, "legacy_pages": 0, "notes_relinked": 0,
                                                  "area_pages_repointed": 0}  # idempotent


def test_adopt_legacy_pages_repoints_area_links(world):
    """Area pages link to entity pages. A link to a removed page follows the redirect to the survivor; one with no
    survivor is dropped; a live link and the survivor are listed once."""
    ctx = world.ctx
    i1, _ = world.item("it_000000000000a1")
    i2, _ = world.item("it_000000000000a2")
    surv = world.entity("person", "Aarav Shah", [i1, i2])
    surv_path = render.write_entity(ctx, surv)
    ctx.con.execute("INSERT INTO entity_redirects(old_path, entity_id) VALUES (?,?)", ("people/old-aarav.md", surv))
    old_fm = {"type": "person", "title": "Old Aarav", "updated": "2026-10-12"}
    body = "## Summary\nx\n\n## Now\n\n## Timeline\n\n## Links\n"
    ctx.wiki.write("people/old-aarav.md", old_fm, body)
    ctx.wiki.write("people/chat-member.md", {**old_fm, "title": "Chat member"}, body)
    ctx.wiki.write("areas/school.md", {"type": "area", "title": "School", "status": "accepted", "updated": "2026-10-12"},
                   "## Summary\nSchool.\n\n## Now\n\n## Timeline\n\n## Links\n- [[people/old-aarav]]\n"
                   f"- [[people/chat-member]]\n- [[{surv_path[:-3]}]]\n")

    render.adopt_legacy_pages(ctx)

    links = section(ctx.wiki.read("areas/school.md").body, "Links").split()
    assert [l for l in links if l.startswith("[[")] == [f"[[{surv_path[:-3]}]]"]
    assert render.lint(ctx)["dead_links"] == 0


def test_adopt_legacy_pages_demotes_an_entity_no_note_names_any_more(world):
    """An imported page whose name no note resolves to (a single first name the new resolver keeps as plain text)
    can never have a page: its file goes, the entity becomes a candidate that keeps its text, and the old address
    still resolves to it."""
    ctx, reg = world.ctx, world.reg
    ghost = world.entity("person", "Kunal", [], path="people/kunal.md", summary="Kept summary.")
    ctx.wiki.write("people/kunal.md", {"type": "person", "title": "Kunal", "updated": "2026-10-12"},
                   "## Summary\nKept summary.\n\n## Now\n\n## Timeline\n\n## Links\n")

    out = render.adopt_legacy_pages(ctx)

    assert out["removed"] == 1 and out["demoted"] == 1 and not ctx.wiki.exists("people/kunal.md")
    e = reg.get(ghost)
    assert e["status"] == "candidate" and e["path"] is None
    assert reg.redirect("people/kunal.md") == ghost
    assert ctx.con.execute("SELECT summary FROM entity_text WHERE entity_id=?", (ghost,)).fetchone()[0] == "Kept summary."
    assert render.lint(ctx)["missing_files"] == 0


def test_lint_does_not_flag_a_known_person_named_like_a_chat(world):
    """The 1:1 chat with someone is titled with their name. That is not a chat name taken for a person: a person
    the user has marked known is exempt from the title check, and an unknown one is still counted."""
    ctx = world.ctx
    i1, _ = world.item("it_000000000000a1", title="Mamma · week 41", source="whatsapp", chat_id="c1")
    ctx.con.execute("UPDATE items SET kind='chat_week' WHERE id=?", (i1,))
    mum = world.entity("person", "Mamma", [i1])
    assert render.lint(ctx)["chat_name_people"] == 1
    ctx.con.execute("UPDATE entities SET known=1 WHERE id=?", (mum,))
    assert render.lint(ctx)["chat_name_people"] == 0
