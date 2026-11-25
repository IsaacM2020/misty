import json

import pytest

from misty_engine.registry import privacy
from misty_engine.registry.store import Registry, RegistryError


def dump(con, ids):
    q = ",".join("?" * len(ids))
    out = {}
    for t, col in (("entities", "id"), ("entity_aliases", "entity_id"), ("entity_handles", "entity_id"),
                   ("mentions", "entity_id"), ("entity_text", "entity_id")):
        out[t] = sorted(tuple(r) for r in con.execute(f"SELECT * FROM {t} WHERE {col} IN ({q})", ids))
    out["redirects"] = sorted(tuple(r) for r in con.execute("SELECT * FROM entity_redirects"))
    return out


def seed(con, add_item, reg):
    a = reg.new_entity("person", "Pranav Asrani", status="active", path="people/pranav-asrani.md")
    b = reg.new_entity("person", "Pranav", status="active", path="people/pranav.md")
    for i in ("it_1", "it_2", "it_3"):
        add_item(i)
    reg.add_alias(a, "Pranav Asrani")
    reg.add_alias(b, "Pranav")
    reg.add_alias(b, "Pranav A")
    reg.add_handle(b, "phone", "+6591234567")
    reg.add_mention("it_1", a, "Pranav Asrani")
    reg.add_mention("it_2", b, "Pranav")
    reg.add_mention("it_3", b, "Pranav A")
    con.execute("INSERT INTO entity_text(entity_id, summary, summary_n) VALUES (?, 'Friend.', 2)", (b,))
    return a, b


def test_merge_moves_everything(con, clock, add_item):
    reg = Registry(con, clock)
    a, b = seed(con, add_item, reg)
    mid = reg.merge(a, b, reason="R4")
    assert reg.get(b)["status"] == "merged" and reg.survivor(b) == a
    assert reg.by_handle("phone", "+6591234567") == a
    assert reg.redirect("people/pranav.md") == a
    assert sorted(reg.mention_items(a)) == ["it_1", "it_2", "it_3"]
    assert {r["alias_norm"] for r in reg.aliases(a)} == {"pranav asrani", "pranav", "pranav a"}
    assert mid > 0


def test_unmerge_restores_everything(con, clock, add_item):
    reg = Registry(con, clock)
    a, b = seed(con, add_item, reg)
    before = dump(con, [a, b])
    mid = reg.merge(a, b, reason="R4")
    assert dump(con, [a, b]) != before
    reg.unmerge(mid)
    assert dump(con, [a, b]) == before


def test_unmerge_with_shared_alias_and_mention_and_chained_redirect(con, clock, add_item):
    reg = Registry(con, clock)
    a, b = seed(con, add_item, reg)
    reg.add_alias(a, "Pranav", n=2)                       # an alias both sides have
    reg.add_mention("it_2", a, "Pranav")                  # a mention both sides have
    c = reg.new_entity("person", "Pran", status="active", path="people/pran.md")
    m0 = reg.merge(b, c, reason="R4")                     # c -> b, redirect people/pran.md -> b
    before = dump(con, [a, b, c])
    mid = reg.merge(a, b, reason="R4")                    # b -> a, c's redirect must follow
    assert reg.redirect("people/pran.md") == a
    reg.unmerge(mid)
    assert dump(con, [a, b, c]) == before
    assert reg.redirect("people/pran.md") == b
    reg.unmerge(m0)
    assert reg.get(c)["status"] == "active" and reg.redirect("people/pran.md") is None


def test_unmerge_twice_refused(con, clock, add_item):
    reg = Registry(con, clock)
    a, b = seed(con, add_item, reg)
    mid = reg.merge(a, b, reason="R4")
    reg.unmerge(mid)
    with pytest.raises(RegistryError):
        reg.unmerge(mid)


def test_pinned_name_survives_merge(con, clock, add_item):
    reg = Registry(con, clock)
    a, b = seed(con, add_item, reg)
    reg.rename(b, "Pran", pinned=True)
    reg.merge(a, b, reason="user")
    assert reg.get(a)["name"] == "Pran" and reg.get(a)["name_pinned"] == 1


def test_find_alias_ignores_merged(con, clock, add_item):
    reg = Registry(con, clock)
    a, b = seed(con, add_item, reg)
    assert reg.find_alias("person", "pranav") == [b]
    reg.merge(a, b, reason="R4")
    assert reg.find_alias("person", "pranav") == [a]


def test_merge_across_kinds_refused(con, clock):
    reg = Registry(con, clock)
    p = reg.new_entity("person", "Python Guy", status="active")
    t = reg.new_entity("topic", "Python", status="active")
    with pytest.raises(RegistryError):
        reg.merge(p, t, reason="x")


def test_ids_are_permanent_and_unique(con, clock):
    reg = Registry(con, clock)
    ids = {reg.new_entity("topic", f"T{i}") for i in range(50)}
    assert len(ids) == 50 and all(i.startswith("en_") and len(i) == 15 for i in ids)


def test_honorific_alias_is_one_token(con, clock):
    reg = Registry(con, clock)
    e = reg.new_entity("person", "Janaki Kota")
    reg.add_alias(e, "Ms. Janaki")
    assert reg.find_alias("person", "janaki") == [e]


def test_private_item(con, settings, add_item):
    add_item("it_c", source="whatsapp", kind="chat_week")
    con.execute("UPDATE items SET meta=? WHERE id='it_c'", (json.dumps({"chat_id": "c1"}),))
    assert privacy.is_private_item(con, settings, "it_c") is False
    settings.set("context_private_chats", ["whatsapp:c1"])
    assert privacy.is_private_item(con, settings, "it_c") is True
    assert privacy.is_private_item(con, settings, "nope") is False


def test_a_page_that_exists_is_never_the_one_that_disappears(con, clock, add_item):
    reg = Registry(con, clock)
    cand = reg.new_entity("person", "Ms Janaki", status="candidate")
    page = reg.new_entity("person", "Janaki Kota", status="active", path="people/janaki-kota.md")
    reg.merge(cand, page, reason="R7")                  # asked the wrong way round
    assert reg.get(page)["status"] == "active" and reg.get(page)["path"] == "people/janaki-kota.md"
    assert reg.get(cand)["status"] == "merged" and reg.survivor(cand) == page


def test_topic_keeps_its_common_spelling_but_a_person_takes_the_fullest_name(con, clock):
    reg = Registry(con, clock)
    t = reg.new_entity("topic", "Exam Schedule", status="active")
    reg.add_alias(t, "Exam Schedule", n=5)
    reg.add_alias(t, "Unit Test 1 schedule", n=1)
    reg.recompute_name(t)
    assert reg.get(t)["name"] == "Exam Schedule"
    p = reg.new_entity("person", "Pranav", status="active")
    reg.add_alias(p, "Pranav", n=9)
    reg.add_alias(p, "Pranav Naveen Asrani", n=1)
    reg.recompute_name(p)
    assert reg.get(p)["name"] == "Pranav Naveen Asrani"
