"""Shared JSON contract: the engine writes these files (UPDATE_CONTRACT=1), the Swift tests decode the same files."""
import json
import os
from pathlib import Path

import pytest

from misty_engine.api import build_methods, status_snapshot
from misty_engine.build import Build
from misty_engine.rpc import RpcServer
from misty_engine.wiki import priorities as pr
from test_context_pack import put

CONTRACT = Path(__file__).resolve().parents[1] / "contract"


def check(name: str, obj) -> None:
    text = json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n"
    path = CONTRACT / f"{name}.json"
    if os.environ.get("UPDATE_CONTRACT") == "1":
        CONTRACT.mkdir(exist_ok=True)
        path.write_text(text)
    assert path.exists(), f"{name}.json missing (run with UPDATE_CONTRACT=1)"
    assert path.read_text() == text, f"{name}.json drifted from the engine output"


@pytest.fixture
def world(fctx, seed):
    fctx.clock.t = 1_792_000_000.0
    s = fctx.con
    s.execute("INSERT INTO sources_state(source, health, last_ok_at, cursor) VALUES ('folders','ok',1791999000,NULL)")
    s.execute("INSERT INTO sources_state(source, health, last_ok_at) VALUES ('imessage','needs_fda',NULL)")
    put(fctx, "areas/school", "area", "School", "School and deadlines.", importance=5)
    put(fctx, "projects/igem", "project", "iGEM", "The iGEM wiki project.", importance=4, links=["people/aarav"],
        now=["write part 3 docs"], timeline=["2026-10-13: wiki sync", "2026-10-02: kickoff"])
    put(fctx, "people/aarav", "person", "Aarav", "Teammate.", importance=3, links=["projects/igem"])
    p = fctx.wiki.read("projects/igem.md")
    fm = dict(p.fm)
    fm.update({"area": "school", "sources": ["it_c000000000000001"]})
    fctx.wiki.write("projects/igem.md", fm, p.body)
    seed("it_c000000000000001", "iGEM wiki notes about the parts", title="igem-notes.md")
    for sid, kind, text, urg, ts in (("sg_00000000000001", "reply", "Reply to aarav@x.com: Wiki sync", "now", 1791999900),
                                     ("sg_00000000000002", "task", "To do: submit form", "today", 1791999800)):
        s.execute("INSERT INTO suggestions(id, item_id, kind, text, urgency, status, sender, created_at, updated_at) "
                  "VALUES (?,?,?,?,?, 'new', ?, ?, ?)",
                  (sid, "it_c000000000000001", kind, text, urg, "aarav@x.com", ts, ts))
    return fctx


def _registry_world(ctx):
    """A small registry wiki for the graph shapes: 7 topics and a project in School, a person who works on the
    project, and four chat-only members folded into one group node."""
    import json as _json
    from misty_engine.registry.store import Registry
    reg = Registry(ctx.con, ctx.clock)
    n = [0]

    def item(kind="mail_thread", source="gmail", chat=None, title="Note", dated=()):
        n[0] += 1
        iid = f"it_ct{n[0]:014d}"
        meta = {"dated_lines": [list(d) for d in dated]}
        if chat:
            meta["chat_id"] = chat
        ctx.con.execute("INSERT INTO items(id, source, kind, locator, title, status, note_path, modified_at, meta, "
                        "updated_at) VALUES (?,?,?,?,?,'done',?, '2026-10-12T00:00:00+00:00', ?, 0)",
                        (iid, source, kind, f"/c/{iid}", title, f"sources/{source}/c{n[0]}.md", _json.dumps(meta)))
        return iid

    def ent(kind, name, folder, ids, area="school", status="active"):
        e = reg.new_entity(kind, name, status=status, path=f"{folder}/{name.lower().replace(' ', '-')}.md"
                           if status == "active" else None)
        ctx.con.execute("UPDATE entities SET area=?, importance=4 WHERE id=?", (area if status == "active" else None, e))
        if status == "active":
            ctx.con.execute("INSERT INTO entity_text(entity_id, summary, summary_n) VALUES (?,?,3)", (e, f"{name}."))
        for i in ids:
            reg.add_mention(i, e, name)
        return e
    shared = [item(dated=[("2026-10-13", "wiki sync")]) for _ in range(3)]
    ent("project", "Wiki project", "projects", shared)
    ent("person", "Aarav Shah", "people", shared)
    for k in range(7):
        ent("topic", f"Exam topic {k}", "topics", [item() for _ in range(3)])
    chat = [item(kind="chat_week", source="whatsapp", chat="c9", title="Study group · 2026-W41") for _ in range(2)]
    for k in range(4):
        ent("person", f"Member {k} Name", "people", chat, status="candidate")


async def test_status_shapes(world):
    check("status", status_snapshot(world))
    world.con.execute("INSERT INTO builds(id, started_at, item_limit, phase, status) "
                      "VALUES ('bd_000000000001', 1791999000, NULL, 'compile', 'running')")
    world.con.execute("INSERT INTO spend(ts, provider, model, job_id, build_id, usd) "
                      "VALUES (1791999100,'openrouter','m',NULL,'bd_000000000001',0.0123)")
    check("status_building", status_snapshot(world))


async def test_suggestions_graph_page_priorities(world):
    m = build_methods(world)
    check("suggestion_list", await m["suggestions.list"]({"status": "new"}))
    _registry_world(world)
    check("graph", await m["graph.get"]({}))
    check("graph_expanded", await m["graph.get"]({"expanded": ["areas/school"]}))
    check("page", await m["page.get"]({"path": "projects/igem.md"}))
    pr.write_draft(world.wiki, [{"text": "Ship the iGEM wiki", "area": "school"}, {"text": "NOI prep"}], world.clock)
    pr.apply(world.wiki, world.clock)
    check("priorities", await m["priorities.get"]({}))
    (world.paths.wiki / "priorities.proposed.md").write_text(
        "---\ntype: priorities\ntitle: Priorities\nstatus: draft\nupdated: 2026-10-14\n---\n\n"
        "## Priorities\n- Ship the iGEM wiki <!-- id:p1111 -->\n- IB reading <!-- id:p2222 -->\n")
    check("priorities_diff", await m["priorities.diff"]({}))


async def test_query_shapes_and_events(world, fake_or):
    m = build_methods(world)
    fake_or.set_answers({"route": ("file", 0.9), "sources": (("files",), 0.9)})
    res = await m["query"]({"text": "igem notes"})
    check("query_search", res)
    fake_or.set_answers({"route": ("ask_wiki", 0.9)})
    fake_or.chat_text("iGEM is the wiki project [[projects/igem]] for school [[areas/school]].")
    check("query_ask", await m["query"]({"text": "what's going on with iGEM"}))
    st = status_snapshot(world)
    check("event_status_changed", {"jsonrpc": "2.0", "method": "status.changed", "params": st})
    check("event_suggestion_new", {"jsonrpc": "2.0", "method": "suggestion.new",
                                   "params": {"id": "sg_00000000000001", "kind": "reply", "urgency": "now"}})
    world.con.execute("INSERT INTO builds(id, started_at, item_limit, phase, status) "
                      "VALUES ('bd_000000000002', 1792000000, NULL, 'compile', 'running')")
    check("event_build_progress", {"jsonrpc": "2.0", "method": "build.progress",
                                   "params": Build(world).status(include_done=False)})


async def test_error_shape(world):
    async def boom(_):
        from misty_engine.errors import Fatal
        raise Fatal("KEY_INVALID")
    srv = RpcServer(Path("/tmp/x.sock"), {"x": boom})
    out = await srv.handle_line(b'{"jsonrpc":"2.0","id":7,"method":"x"}')
    check("error", out)


async def test_context_and_lookup_shapes(world):
    from misty_engine.registry.store import Registry
    m = build_methods(world)
    check("context", await m["context"]({"topic": "iGEM wiki", "budget_tokens": 800}))
    reg = Registry(world.con, world.clock)
    e = reg.new_entity("person", "Aarav Shah", status="active", path="people/aarav-shah.md", known=True)
    reg.add_alias(e, "Aarav")
    reg.add_handle(e, "phone", "+6591234567")
    out = await m["entities.lookup"]({"text": "Aarav", "kind": "person"})
    for c in out["candidates"]:
        c["id"] = "en_000000000000"                                  # ids are random: the shape is what is pinned
    check("lookup", out)
