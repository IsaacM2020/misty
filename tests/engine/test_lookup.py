import pytest

from misty_engine.registry import render
from misty_engine.registry.lookup import lookup
from misty_engine.registry.store import Registry
from test_render_lint import world  # noqa: F401


def person(world, name, n, aliases=(), path=None):
    items = [world.item(f"it_lk{abs(hash((name, i))) % 10**12:012d}")[0] for i in range(n)]
    e = world.entity("person", name, items, path=path or f"people/{name.lower().replace(' ', '-')}.md")
    for a in aliases:
        Registry(world.ctx.con, world.ctx.clock).add_alias(e, a)
    return e


def test_ms_janaki_is_not_ambiguous_when_one_candidate(world):
    j = person(world, "Janaki Kota", 3)
    r = lookup(world.ctx, "Ms Janaki", kind="person")
    assert r["candidates"][0]["id"] == j and r["candidates"][0]["name"] == "Janaki Kota"
    assert r["ambiguous"] is False and r["candidates"][0]["confidence"] == 0.8


def test_two_pranavs_are_ambiguous(world):
    person(world, "Pranav Asrani", 3)
    person(world, "Pranav Kumar", 3)
    r = lookup(world.ctx, "Pranav", kind="person")
    assert r["ambiguous"] is True and len(r["candidates"]) == 2


def test_exact_full_name_is_confident_and_a_merged_name_resolves_to_the_survivor(world):
    a = person(world, "Pranav Asrani", 3)
    b = person(world, "Pranav", 2)
    Registry(world.ctx.con, world.ctx.clock).merge(a, b, reason="R7")
    r = lookup(world.ctx, "Pranav Asrani")
    assert r["candidates"][0]["id"] == a and r["candidates"][0]["confidence"] == 0.95
    assert [c["id"] for c in lookup(world.ctx, "Pranav", kind="person")["candidates"]] == [a]


def test_facts_and_local_handles_are_returned(world):
    a = person(world, "Aarav Shah", 2)
    Registry(world.ctx.con, world.ctx.clock).add_handle(a, "phone", "+6591234567")
    c = lookup(world.ctx, "Aarav Shah")["candidates"][0]
    assert c["facts"]["handles"] == [{"type": "phone", "value": "+6591234567"}] and "last_interaction" in c["facts"]


def test_kind_filter_and_empty_text(world):
    person(world, "Python Lee", 2)
    t = world.entity("topic", "Python", [world.item("it_lt0000000000a1")[0]], path="topics/python.md")
    r = lookup(world.ctx, "Python", kind="topic")
    assert [c["id"] for c in r["candidates"]] == [t]
    assert lookup(world.ctx, "  ") == {"candidates": [], "ambiguous": False}


def test_near_names_come_from_vectors_with_half_the_cosine(world):
    t = world.entity("topic", "SAT preparation", [world.item("it_lt0000000000b2")[0]], path="topics/sat.md")
    world.ctx.con.execute("INSERT INTO entity_vec(entity_id, vec) VALUES (?, ?)", (t, _vec(world.ctx, "SAT preparation")))
    r = lookup(world.ctx, "SAT preparation tests", kind="topic")
    assert r["candidates"] and r["candidates"][0]["id"] == t and r["candidates"][0]["confidence"] <= 0.5


def _vec(ctx, text):
    import numpy as np
    return np.asarray(ctx.embedder.embed_documents([text])[0], dtype=np.float32).tobytes()
