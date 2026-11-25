import json
import math

import pytest

from misty_engine.api import build_methods
from misty_engine.registry.store import Registry
from misty_engine.wiki import graph as G
from misty_engine.wiki.graph import build_graph, graph_score
from test_render_lint import world  # noqa: F401

NOTE = ("---\ntype: source\ntitle: t\nitem_id: x\nsource: gmail\nupdated: 2026-10-14\n---\n\n## Summary\ns\n\n"
        "## Key facts\n- f\n\n## People\n- (none)\n\n## Links\n- (none)\n")


def make_area(ctx, slug, title, importance=3):
    fm = {"type": "area", "title": title, "aliases": [], "area": None, "importance": importance,
          "status": "accepted", "updated": "2026-10-14", "sources": [], "notes": 0}
    ctx.wiki.write(f"areas/{slug}.md", fm, "## Summary\nOne line.\n\n## Now\n\n## Timeline\n\n## Links\n")


class Maker:
    """Bulk builder: entities with pages (summaries), notes in public chats or mail, optional areas."""

    def __init__(self, ctx):
        self.ctx, self.reg, self.n = ctx, Registry(ctx.con, ctx.clock), 0

    def item(self, source="gmail", kind="mail_thread", chat_id=None, dated=(), modified="2026-10-01T00:00:00+00:00",
             title=None):
        self.n += 1
        iid = f"it_gr{self.n:014d}"
        meta = {"dated_lines": [list(d) for d in dated]}
        if chat_id:
            meta["chat_id"] = chat_id
        self.ctx.con.execute(
            "INSERT INTO items(id, source, kind, locator, title, status, note_path, modified_at, meta, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,0)", (iid, source, kind, f"/x/{iid}", title or f"Note {self.n}", "done",
                                             f"sources/{source}/n{self.n}.md", modified, json.dumps(meta)))
        return iid

    def entity(self, kind, name, n_notes, *, area=None, importance=3, summary="A real summary.", active=True,
               items=None, **item_kw):
        slug = name.lower().replace(" ", "-")
        e = self.reg.new_entity(kind, name, status="active" if active else "candidate",
                                path=f"{ {'person': 'people', 'project': 'projects', 'topic': 'topics'}[kind] }/{slug}.md" if active else None)
        self.ctx.con.execute("UPDATE entities SET area=?, importance=? WHERE id=?", (area, importance, e))
        if summary and active:
            self.ctx.con.execute("INSERT INTO entity_text(entity_id, summary, summary_n) VALUES (?,?,?)",
                                 (e, summary, n_notes))
        for iid in (items if items is not None else [self.item(**item_kw) for _ in range(n_notes)]):
            self.reg.add_mention(iid, e, name)
        return e


@pytest.fixture
def g(world):
    world.m = Maker(world.ctx)
    return world


def by_id(graph):
    return {n["id"]: n for n in graph["nodes"]}


def test_graph_score_weights_and_recency():
    assert graph_score(50, 0, 10, 5) == pytest.approx(1.0)
    assert graph_score(0, None, 0, 0) == 0.0
    assert graph_score(5, 30, 0, 3) > graph_score(5, 200, 0, 3) > graph_score(5, 400, 0, 3)
    assert graph_score(5, 365, 0, 3) == pytest.approx(0.35 * math.log1p(5) / math.log1p(50) + 0.15 * 3 / 5)


def test_default_view_is_me_every_area_and_top_five_each(g):
    ctx, m = g.ctx, g.m
    for slug in ("school", "igem"):
        make_area(ctx, slug, slug.title(), 4)
    for i in range(9):
        m.entity("topic", f"School topic {i}", 3 + i, area="school")
    for i in range(3):
        m.entity("project", f"Igem project {i}", 3 + i, area="igem")
    out = build_graph(ctx)
    nodes = by_id(out)
    assert nodes["me"]["type"] == "self" and {"areas/school", "areas/igem"} <= set(nodes)
    school = [n for n in out["nodes"] if n.get("area") == "school"]
    assert len(school) == G.DEFAULT_TOP
    assert {n["label"] for n in school} == {f"School topic {i}" for i in range(4, 9)}      # the most noted ones
    assert nodes["areas/school"]["more"] == 4 and nodes["areas/school"]["expanded"] is False
    assert nodes["areas/igem"]["more"] == 0


def test_expanding_an_area_shows_twelve_and_the_right_more(g):
    ctx, m = g.ctx, g.m
    make_area(ctx, "school", "School", 4)
    for i in range(20):
        m.entity("topic", f"Topic {i:02d}", 3 + i, area="school")
    out = build_graph(ctx, expanded=["areas/school"])
    shown = [n for n in out["nodes"] if n.get("area") == "school"]
    assert len(shown) == G.EXPAND_TOP
    a = by_id(out)["areas/school"]
    assert a["more"] == 8 and a["expanded"] is True


def test_chat_only_people_fold_into_one_group_node(g):
    ctx, m = g.ctx, g.m
    make_area(ctx, "school", "School")
    chat_items = [m.item(source="whatsapp", kind="chat_week", chat_id="c9", title="MC SA2 OVER · 2026-W20")
                  for _ in range(2)]
    for i in range(4):
        m.entity("person", f"Member {i} Name", 0, active=False, items=chat_items, summary=None)
    m.entity("topic", "Real topic", 3, area="school")
    out = build_graph(ctx)
    groups = [n for n in out["nodes"] if n["type"] == "group"]
    assert len(groups) == 1 and groups[0]["members"] == 4 and groups[0]["label"] == "MC SA2 OVER"
    assert not any(n["label"].startswith("Member ") for n in out["nodes"])


def test_private_chat_people_and_counts_never_show(g):
    ctx, m = g.ctx, g.m
    make_area(ctx, "school", "School")
    priv = [m.item(source="whatsapp", kind="chat_week", chat_id="cp", title="Secret chat · 2026-W20",
                   dated=[("2026-10-14", "PRIVATE-DATE")]) for _ in range(4)]
    m.entity("person", "Dev Rao", 4, area="school", items=priv)
    pub = [m.item(source="gmail", dated=[("2026-10-14", "public")]) for _ in range(3)]
    e = m.entity("topic", "Exams", 3, area="school", items=pub + priv)
    ctx.settings.set("context_private_chats", ["whatsapp:cp"])
    out = build_graph(ctx)
    ids = by_id(out)
    assert "people/dev-rao" not in ids and not any(n["type"] == "group" for n in out["nodes"])
    assert ids["topics/exams"]["sources"] == 3            # the 4 private notes do not count
    assert ids["topics/exams"]["glow"] == pytest.approx(0.6)       # 3 public dated lines in the week, not 7


def test_edge_kinds_and_per_node_limit(g):
    ctx, m = g.ctx, g.m
    make_area(ctx, "school", "School")
    shared = [m.item() for _ in range(4)]
    p = m.entity("person", "Aarav Shah", 0, area="school", items=shared)
    projects = [m.entity("project", f"Proj {i}", 0, area="school", items=shared[: 2 + (i % 2)] + [m.item()] * 0)
                for i in range(5)]
    t1 = m.entity("topic", "Topic One", 0, area="school", items=shared)
    t2 = m.entity("topic", "Topic Two", 0, area="school", items=shared)
    out = build_graph(ctx, expanded=["areas/school"])
    kinds = {e["kind"] for e in out["edges"]}
    assert kinds <= {"belongs", "involves", "related"} and {"involves", "related"} <= kinds
    deg = {}
    for e in out["edges"]:
        if e["kind"] != "belongs":
            deg[e["source"]] = deg.get(e["source"], 0) + 1
            deg[e["target"]] = deg.get(e["target"], 0) + 1
    assert deg and max(deg.values()) <= G.MAX_EDGES_PER_NODE
    inv = [e for e in out["edges"] if e["kind"] == "involves"]
    assert all(e["weight"] >= 2 for e in inv)


def test_focus_includes_a_low_score_entity_and_expands_its_area(g):
    ctx, m = g.ctx, g.m
    make_area(ctx, "school", "School")
    for i in range(10):
        m.entity("topic", f"Strong {i}", 10 + i, area="school", importance=5)
    m.entity("topic", "Obscure thing", 3, area="school", importance=1, modified="2024-01-01T00:00:00+00:00")
    plain = build_graph(ctx)
    assert "topics/obscure-thing" not in by_id(plain)
    out = build_graph(ctx, focus="topics/obscure-thing")
    assert "topics/obscure-thing" in by_id(out) and by_id(out)["areas/school"]["expanded"] is True


def test_search_returns_alias_matches_with_their_areas_expanded(g):
    ctx, m = g.ctx, g.m
    make_area(ctx, "school", "School")
    make_area(ctx, "igem", "iGEM")
    for i in range(8):
        m.entity("topic", f"Filler {i}", 5 + i, area="school")
    e = m.entity("person", "Janaki Kota", 3, area="igem", importance=1)
    Registry(ctx.con, ctx.clock).add_alias(e, "Ms Janaki")
    out = build_graph(ctx, q="Ms Janaki")
    ids = by_id(out)
    assert "people/janaki-kota" in ids and ids["areas/igem"]["expanded"] is True
    assert sum(1 for n in out["nodes"] if n["type"] == "person") <= G.SEARCH_MAX


def test_thin_entities_and_unsummarised_ones_are_not_nodes(g):
    ctx, m = g.ctx, g.m
    make_area(ctx, "school", "School")
    m.entity("topic", "Two sources only", 2, area="school")
    m.entity("topic", "No summary", 5, area="school", summary=None)
    m.entity("person", "Mom", 1, area="school")                              # a known name qualifies with one
    ctx.con.execute("UPDATE entities SET known=1 WHERE name='Mom'")
    ids = by_id(build_graph(ctx))
    assert "topics/two-sources-only" not in ids and "topics/no-summary" not in ids and "people/mom" in ids


def test_a_thousand_entities_stay_under_sixty_nodes_by_default(g):
    ctx, m = g.ctx, g.m
    for k in range(9):
        make_area(ctx, f"area{k}", f"Area {k}", 3)
    for i in range(1000):
        m.entity("topic" if i % 3 else "project", f"Thing {i:04d}", 3, area=f"area{i % 9}", importance=3,
                 items=[m.item(), m.item(), m.item()])
    out = build_graph(ctx)
    assert len(out["nodes"]) <= 60
    assert sum(1 for n in out["nodes"] if n["type"] == "area") == 9
    assert all(by_id(out)[f"areas/area{k}"]["more"] > 0 for k in range(9))


@pytest.mark.asyncio
async def test_graph_get_passes_params_through_the_rpc(g):
    ctx, m = g.ctx, g.m
    make_area(ctx, "school", "School")
    for i in range(7):
        m.entity("topic", f"Topic {i}", 3 + i, area="school")
    api = build_methods(ctx)
    base = await api["graph.get"]({})
    exp = await api["graph.get"]({"expanded": ["areas/school"]})
    assert len(exp["nodes"]) > len(base["nodes"])
    hit = await api["graph.get"]({"q": "Topic 0"})
    assert "topics/topic-0" in {n["id"] for n in hit["nodes"]}
