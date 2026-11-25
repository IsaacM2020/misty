import itertools
import json

import pytest

from misty_engine.errors import CapHit, NetworkDown
from misty_engine.registry import sweep as S
from misty_engine.registry.store import Registry
from misty_engine.registry.sweep import resolve_sweep


def people(ctx, kind="person", statuses=("candidate", "active")):
    q = f"SELECT name, status, pending FROM entities WHERE kind=? AND status IN ({','.join('?' * len(statuses))})"
    return sorted(tuple(r) for r in ctx.con.execute(q, (kind, *statuses)))


def count(ctx, table="entities"):
    return ctx.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def seed_entity(ctx, kind, name, items, *, status="active", chat_id=None, origin="compile"):
    """An existing entity with `items` mention rows (each in its own note)."""
    reg = Registry(ctx.con, ctx.clock)
    eid = reg.new_entity(kind, name, status=status, origin=origin, path=f"{kind}s/{name.lower().replace(' ', '-')}.md")
    for i in range(items):
        iid = f"it_seed{abs(hash((name, i))) % 10**9:09d}"
        ctx.con.execute("INSERT OR IGNORE INTO items(id, source, kind, locator, title, status, meta, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (iid, "whatsapp", "chat_week", f"/s/{iid}", "t", "done",
                         json.dumps({"chat_id": chat_id} if chat_id else {}), ctx.clock.now()))
        reg.add_mention(iid, eid, name)
    reg.add_alias(eid, name, n=items)
    return eid


SAME = {"pairs": [{"id": i, "verdict": "same", "why": "same person"} for i in range(1, 30)]}
DIFF = {"pairs": [{"id": i, "verdict": "different", "why": "x"} for i in range(1, 30)]}
UNSURE = {"pairs": [{"id": i, "verdict": "unsure", "why": "x"} for i in range(1, 30)]}


@pytest.mark.asyncio
async def test_sweep_order_independent(ctx_runner, make_items):
    names = ["Pranav", "Pranav Asrani Real", "Pranav Naveen Asrani"]
    results = []
    for order in itertools.permutations(names):
        ctx, _ = ctx_runner(SAME)
        make_items(ctx, [(f"it_{i:016d}", {"people": [n]}) for i, n in enumerate(order)])
        await resolve_sweep(ctx)
        results.append(people(ctx, statuses=("candidate", "active")))
    assert all(r == results[0] for r in results) and len(results[0]) == 1
    assert results[0][0][0] == "Pranav Naveen Asrani"


@pytest.mark.asyncio
async def test_sweep_keeps_apart_what_golden_says_apart(ctx_runner, make_items):
    """Pranav Asrani and Naveen Asrani are different people even though 'Pranav Naveen Asrani' fits both."""
    names = ["Pranav Asrani", "Naveen Asrani", "Pranav Naveen Asrani", "Pranav"]
    seen = []
    for order in itertools.permutations(names):
        ctx, _ = ctx_runner(SAME, SAME, SAME)
        make_items(ctx, [(f"it_{i:016d}", {"people": [n]}) for i, n in enumerate(order)])
        await resolve_sweep(ctx)
        rows = people(ctx)
        seen.append(rows)
        # no entity holds both spellings
        for eid in [r["id"] for r in ctx.con.execute("SELECT id FROM entities WHERE kind='person'")]:
            al = {a["alias_norm"] for a in ctx.con.execute("SELECT alias_norm FROM entity_aliases WHERE entity_id=?", (eid,))}
            assert not ({"pranav asrani", "naveen asrani"} <= al)
    assert all(s == seen[0] for s in seen)


@pytest.mark.asyncio
async def test_sweep_idempotent_no_new_entity_for_known_name(ctx_runner, make_items):
    ctx, _ = ctx_runner()
    make_items(ctx, [("it_0000000000000001", {"people": ["Aarav Shah"]}),
                     ("it_0000000000000002", {"people": ["Aarav Shah"]})])
    await resolve_sweep(ctx)
    n = count(ctx)
    make_items(ctx, [("it_0000000000000003", {"people": ["aarav  shah"]})])
    await resolve_sweep(ctx)
    await resolve_sweep(ctx)
    assert count(ctx) == n
    assert count(ctx, "mentions") == 3


@pytest.mark.asyncio
async def test_new_mention_of_existing_entity_never_creates_an_entity(ctx_runner, make_items):
    ctx, _ = ctx_runner()
    e = seed_entity(ctx, "person", "Aarav Shah", 3)
    before = count(ctx)
    make_items(ctx, [("it_0000000000000009", {"people": ["Aarav Shah", "Ms Janaki Kota"]})])
    r = await resolve_sweep(ctx)
    assert count(ctx) == before + 1                      # only Janaki is new
    assert r.resolved == 1 and r.created == 1
    assert "it_0000000000000009" in Registry(ctx.con, ctx.clock).mention_items(e)


@pytest.mark.asyncio
async def test_judge_unavailable_blocks_page_not_data(ctx_runner, make_items, monkeypatch):
    ctx, runner = ctx_runner()
    pa = seed_entity(ctx, "person", "Pranav Asrani", 3, chat_id="c1")
    pn = seed_entity(ctx, "person", "Pranav Naveen Asrani", 2, chat_id="c1")
    make_items(ctx, [("it_0000000000000001", {"people": ["Pranav"], "chat_id": "c1"})])

    async def boom(*a, **k):
        raise NetworkDown()

    monkeypatch.setattr("misty_engine.registry.sweep.judge_pairs", boom)
    snap = ctx.con.execute("SELECT id, status, pending FROM entities ORDER BY id").fetchall()
    with pytest.raises(NetworkDown):
        await resolve_sweep(ctx)
    assert [tuple(r) for r in ctx.con.execute("SELECT id, status, pending FROM entities ORDER BY id")] == \
           [tuple(r) for r in snap]                                    # nothing activated, nothing duplicated
    assert ctx.con.execute("SELECT status FROM items WHERE id='it_0000000000000001'").fetchone()[0] == "embedded"
    monkeypatch.undo()                                                 # network back; the judge says same
    runner.script.append(SAME)
    r = await resolve_sweep(ctx)
    assert ctx.con.execute("SELECT status FROM items WHERE id='it_0000000000000001'").fetchone()[0] == "done"
    assert r.pending == 0 and count(ctx, "mentions") >= 3 + 2 + 1


@pytest.mark.asyncio
async def test_cap_hit_mid_sweep_parks_new_cluster_as_pending(ctx_runner, make_items, monkeypatch):
    """A topic in the similarity band needs a verdict; with the judge capped the entity exists but is pending."""
    ctx, _ = ctx_runner()
    seed_entity(ctx, "topic", "SAT Practice", 3)
    make_items(ctx, [("it_0000000000000001", {"topics": ["SAT Preparation"]}),
                     ("it_0000000000000002", {"topics": ["SAT Preparation"]})])
    monkeypatch.setattr(S, "JUDGE_COS", 0.0)                           # everything similar-ish is a question

    async def capped(*a, **k):
        raise CapHit("CAP_PURPOSE:judge")

    monkeypatch.setattr("misty_engine.registry.sweep.judge_pairs", capped)
    with pytest.raises(CapHit):
        await resolve_sweep(ctx)
    rows = ctx.con.execute("SELECT name, status, pending FROM entities WHERE name='SAT Preparation'").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "candidate" and rows[0]["pending"] == 1
    assert count(ctx, "mentions") == 3 + 2                              # seeded 3 + both new, none lost
    monkeypatch.undo()
    monkeypatch.setattr(S, "JUDGE_COS", 0.0)
    ctx.runner.script.append(SAME)
    r = await resolve_sweep(ctx)                                        # the next sweep revisits pending ones
    assert r.pending == 0
    live = ctx.con.execute("SELECT name FROM entities WHERE kind='topic' AND status != 'merged'").fetchall()
    assert len(live) == 1


@pytest.mark.asyncio
async def test_chat_names_never_become_people(ctx_runner, make_items):
    ctx, _ = ctx_runner()
    make_items(ctx, [("it_0000000000000001", {"people": ["MC SA2 OVER chat member", "Aarav Shah", "MC SA2 OVER"]},
                      {"chat_title": "MC SA2 OVER", "chat_kind": "group"})])
    r = await resolve_sweep(ctx)
    assert r.rejected.get("CHAT_WORD") == 1 and r.rejected.get("CHAT_TITLE") == 1
    assert [p[0] for p in people(ctx)] == ["Aarav Shah"]


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(ctx_runner, make_items):
    ctx, runner = ctx_runner()
    make_items(ctx, [("it_0000000000000001", {"people": ["Aarav Shah"]})])
    r = await resolve_sweep(ctx, dry_run=True)
    assert r.mentions == 1 and r.created == 1
    assert count(ctx) == 0 and count(ctx, "mentions") == 0 and runner.calls == []
    assert ctx.con.execute("SELECT status FROM items").fetchone()[0] == "embedded"


@pytest.mark.asyncio
async def test_one_token_attaches_to_the_unique_cluster_else_stays_plain(ctx_runner, make_items):
    ctx, _ = ctx_runner()
    make_items(ctx, [("it_0000000000000001", {"people": ["Aarav Shah"]}),
                     ("it_0000000000000002", {"people": ["Aarav"]}),
                     ("it_0000000000000003", {"people": ["Rahul"]})])
    r = await resolve_sweep(ctx)
    assert [p[0] for p in people(ctx)] == ["Aarav Shah"] and r.plain == 1
    assert count(ctx, "mentions") == 2


@pytest.mark.asyncio
async def test_kind_isolation_person_and_topic_python(ctx_runner, make_items):
    ctx, _ = ctx_runner(SAME)
    make_items(ctx, [("it_0000000000000001", {"people": ["Python Lee"], "topics": ["Python"]}),
                     ("it_0000000000000002", {"topics": ["Python"]})])
    await resolve_sweep(ctx)
    kinds = sorted(r["kind"] for r in ctx.con.execute("SELECT kind FROM entities WHERE status != 'merged'"))
    assert kinds == ["person", "topic"]


@pytest.mark.asyncio
async def test_topic_band_same_merges_different_keeps_apart(ctx_runner, make_items, monkeypatch):
    monkeypatch.setattr(S, "JUDGE_COS", 0.0)
    ctx, runner = ctx_runner(SAME)
    seed_entity(ctx, "topic", "Exam Dates", 3)
    make_items(ctx, [("it_0000000000000001", {"topics": ["Exam Schedule"]}),
                     ("it_0000000000000002", {"topics": ["Exam Schedule"]})])
    r = await resolve_sweep(ctx)
    assert r.merges.get("R7") == 1 and r.judge_pairs == 1
    assert len(ctx.con.execute("SELECT 1 FROM entities WHERE kind='topic' AND status != 'merged'").fetchall()) == 1

    ctx2, _ = ctx_runner(DIFF)
    seed_entity(ctx2, "topic", "Exam Dates", 3)
    make_items(ctx2, [("it_0000000000000001", {"topics": ["Exam Schedule"]}),
                      ("it_0000000000000002", {"topics": ["Exam Schedule"]})])
    r2 = await resolve_sweep(ctx2)
    assert not r2.merges and r2.pending == 0
    assert len(ctx2.con.execute("SELECT 1 FROM entities WHERE kind='topic' AND status != 'merged'").fetchall()) == 2


@pytest.mark.asyncio
async def test_unsure_stays_apart_and_pending(ctx_runner, make_items, monkeypatch):
    monkeypatch.setattr(S, "JUDGE_COS", 0.0)
    ctx, _ = ctx_runner(UNSURE)
    seed_entity(ctx, "topic", "Exam Dates", 3)
    make_items(ctx, [("it_0000000000000001", {"topics": ["Exam Schedule"]}),
                     ("it_0000000000000002", {"topics": ["Exam Schedule"]})])
    r = await resolve_sweep(ctx)
    assert r.pending == 1 and not r.merges and r.qualifying == []
    row = ctx.con.execute("SELECT status, pending FROM entities WHERE name='Exam Schedule'").fetchone()
    assert (row["status"], row["pending"]) == ("candidate", 1)


@pytest.mark.asyncio
async def test_qualification_needs_two_public_sources_or_known(ctx_runner, make_items):
    ctx, _ = ctx_runner()
    ctx.settings.set("context_private_chats", ["whatsapp:cp"])
    make_items(ctx, [
        ("it_0000000000000001", {"people": ["Aarav Shah"], "chat_id": "c1"}),
        ("it_0000000000000002", {"people": ["Aarav Shah"], "chat_id": "cp"}),      # private
        ("it_0000000000000003", {"people": ["Priya Rao"], "chat_id": "c1"}),
        ("it_0000000000000004", {"people": ["Priya Rao"], "chat_id": "c2"}),
    ])
    r = await resolve_sweep(ctx)
    names = {ctx.con.execute("SELECT name FROM entities WHERE id=?", (e,)).fetchone()[0] for e in r.qualifying}
    assert names == {"Priya Rao"}                                      # Aarav has 1 public source only


@pytest.mark.asyncio
async def test_known_name_qualifies_with_one_source(ctx_runner, make_items):
    ctx, _ = ctx_runner()
    seed_entity(ctx, "person", "Mom", 0, status="candidate", origin="contact")
    make_items(ctx, [("it_0000000000000001", {"people": ["Mom"]})])
    r = await resolve_sweep(ctx)
    assert len(r.qualifying) == 1 and r.resolved == 1


@pytest.mark.asyncio
async def test_handles_are_learned_for_the_counterpart(ctx_runner, make_items):
    ctx, _ = ctx_runner()
    make_items(ctx, [("it_0000000000000001", {"people": ["Pranav"], "chat_id": "any;-;+6591234567"},
                      {"chat_kind": "1to1", "counterpart": {"name": "Pranav Asrani", "from": "contacts",
                                                            "handle": ["phone", "+6591234567"]}})])
    await resolve_sweep(ctx)
    reg = Registry(ctx.con, ctx.clock)
    eid = ctx.con.execute("SELECT id FROM entities WHERE name='Pranav Asrani'").fetchone()[0]
    assert reg.by_handle("phone", "+6591234567") == eid
    assert [p[0] for p in people(ctx)] == ["Pranav Asrani"]            # 'Pranav' attached to the counterpart (R3)
    row = ctx.con.execute("SELECT origin, known FROM entities WHERE id=?", (eid,)).fetchone()
    assert (row["origin"], row["known"]) == ("contact", 1)            # a Contacts name is a known name


def test_mentions_for_item_has_context(ctx_runner, make_items):
    ctx, _ = ctx_runner()
    make_items(ctx, [("it_0000000000000001", {"people": ["Aarav"], "projects": ["iGEM"], "chat_id": "c9"},
                      {"title": "Study group · 2026-W20", "chat_kind": "group"})])
    row = ctx.con.execute("SELECT * FROM items").fetchone()
    ms = S.mentions_for_item(ctx, row)
    assert {(m.kind, m.raw) for m in ms} == {("person", "Aarav"), ("project", "iGEM")}
    assert ms[0].ctx.chat_id == "c9" and ms[0].ctx.chat_title == "Study group" and ms[0].ctx.chat_kind == "group"


@pytest.mark.asyncio
async def test_two_spellings_in_one_note_are_asked_about_never_folded_blindly(ctx_runner, make_items, monkeypatch):
    """The compiler sometimes writes both spellings of one topic in one note: the judge decides, no auto-merge."""
    monkeypatch.setattr(S, "JUDGE_COS", 0.0)
    monkeypatch.setattr(S, "AUTO_COS", 0.0)
    monkeypatch.setattr(S, "AUTO_JACCARD", 0.0)
    ctx, runner = ctx_runner(SAME)
    make_items(ctx, [("it_0000000000000001", {"topics": ["Exam Dates", "Exam Schedule"]}),
                     ("it_0000000000000002", {"topics": ["Exam Dates"]}),
                     ("it_0000000000000003", {"topics": ["Exam Schedule"]})])
    r = await resolve_sweep(ctx)
    assert "R6" not in r.merges and r.merges.get("R7") == 1 and len(runner.calls) == 1
    assert len(ctx.con.execute("SELECT 1 FROM entities WHERE kind='topic' AND status != 'merged'").fetchall()) == 1


@pytest.mark.asyncio
async def test_unrelated_names_without_a_shared_word_are_not_asked_about(ctx_runner, make_items, monkeypatch):
    monkeypatch.setattr(S, "JUDGE_COS", 0.0)
    ctx, runner = ctx_runner(SAME)
    make_items(ctx, [("it_0000000000000001", {"topics": ["Chemistry"]}),
                     ("it_0000000000000002", {"topics": ["Football"]})])
    r = await resolve_sweep(ctx)
    assert runner.calls == [] and r.judge_pairs_needed == 0 and not r.merges


@pytest.mark.asyncio
async def test_each_name_is_compared_with_a_few_neighbours_only(ctx_runner, make_items, monkeypatch):
    monkeypatch.setattr(S, "JUDGE_COS", 0.0)
    ctx, _ = ctx_runner()
    make_items(ctx, [(f"it_{i:016d}", {"topics": [f"Exam Topic {i}"]}) for i in range(1, 13)])
    r = await resolve_sweep(ctx, cache_only=True)
    assert r.judge_pairs_needed <= 12 * S.TOP_K and S.TOP_K == 5


@pytest.mark.asyncio
async def test_a_waiting_mention_does_not_hold_back_the_rest_of_its_note(ctx_runner, make_items, monkeypatch):
    ctx, runner = ctx_runner()
    seed_entity(ctx, "person", "Pranav Asrani", 3, chat_id="c1")
    seed_entity(ctx, "person", "Pranav Naveen Asrani", 2, chat_id="c1")
    make_items(ctx, [("it_0000000000000001", {"people": ["Pranav", "Aarav Shah"], "chat_id": "c1"})])

    async def boom(*a, **k):
        raise NetworkDown()

    monkeypatch.setattr("misty_engine.registry.sweep.judge_pairs", boom)
    with pytest.raises(NetworkDown):
        await resolve_sweep(ctx)
    names_ = {r[0] for r in ctx.con.execute(
        "SELECT e.name FROM mentions m JOIN entities e ON e.id = m.entity_id WHERE m.item_id='it_0000000000000001'")}
    assert names_ == {"Aarav Shah"}                                    # decided now, 'Pranav' waits
    assert ctx.con.execute("SELECT status FROM items WHERE id='it_0000000000000001'").fetchone()[0] == "embedded"
    monkeypatch.undo()
    runner.script.append(SAME)
    r = await resolve_sweep(ctx)
    assert r.deferred == 0
    assert ctx.con.execute("SELECT status FROM items WHERE id='it_0000000000000001'").fetchone()[0] == "done"
    assert count(ctx, "mentions") >= 3 + 2 + 2


@pytest.mark.asyncio
async def test_numbered_things_are_never_folded_without_the_judge(ctx_runner, make_items, monkeypatch):
    """Chapter 3 and Chapter 4 look alike to any embedder. Different numbers or letters: ask, never auto-merge."""
    monkeypatch.setattr(S, "AUTO_COS", 0.0)
    monkeypatch.setattr(S, "AUTO_JACCARD", 0.0)
    monkeypatch.setattr(S, "JUDGE_COS", 0.0)
    ctx, runner = ctx_runner(DIFF)
    make_items(ctx, [("it_0000000000000001", {"topics": ["Chapter 3 Notes"]}),
                     ("it_0000000000000002", {"topics": ["Chapter 4 Notes"]}),
                     ("it_0000000000000003", {"topics": ["Chapter 3 Notes"]}),
                     ("it_0000000000000004", {"topics": ["Chapter 4 Notes"]})])
    r = await resolve_sweep(ctx)
    assert "R6" not in r.merges and not r.merges
    assert len(ctx.con.execute("SELECT 1 FROM entities WHERE kind='topic' AND status != 'merged'").fetchall()) == 2


@pytest.mark.asyncio
async def test_private_chat_text_never_goes_into_a_judge_prompt(ctx_runner, make_items):
    ctx, runner = ctx_runner(SAME)
    ctx.settings.set("context_private_chats", ["whatsapp:cp"])
    seed_entity(ctx, "person", "Pranav Asrani", 3)
    seed_entity(ctx, "person", "Pranav Naveen Asrani", 3)
    make_items(ctx, [("it_0000000000000001", {"people": ["Pranav"], "chat_id": "cp"},
                      {"summary": "SECRET-PRIVATE-SENTENCE about a private matter."})])
    await resolve_sweep(ctx)
    assert runner.calls and all("SECRET-PRIVATE-SENTENCE" not in c["parts"][0] for c in runner.calls)
    pub = ctx_runner(SAME)
    ctx2, runner2 = pub
    seed_entity(ctx2, "person", "Pranav Asrani", 3)
    seed_entity(ctx2, "person", "Pranav Naveen Asrani", 3)
    make_items(ctx2, [("it_0000000000000001", {"people": ["Pranav"], "chat_id": "cp"},
                       {"summary": "A public sentence about the project."})])
    await resolve_sweep(ctx2)
    assert any("A public sentence" in c["parts"][0] for c in runner2.calls)       # the snippet is normally sent


@pytest.mark.asyncio
async def test_counterpart_from_the_chat_title_is_a_chat_origin_and_a_phone_only_chat_still_gets_a_handle(
        ctx_runner, make_items):
    ctx, _ = ctx_runner()
    make_items(ctx, [("it_0000000000000001", {"people": [], "chat_id": "6598765432@s.whatsapp.net"},
                      {"chat_kind": "1to1", "counterpart": {"name": None, "from": "chat",
                                                            "handle": ["phone", "+6598765432"]}}),
                     ("it_0000000000000002", {"people": [], "chat_id": "a@s.whatsapp.net"},
                      {"chat_kind": "1to1", "counterpart": {"name": "Arjun", "from": "chat",
                                                            "handle": ["phone", "+6591112222"]}})])
    await resolve_sweep(ctx)
    row = ctx.con.execute("SELECT origin, known FROM entities WHERE name='Arjun'").fetchone()
    assert (row["origin"], row["known"]) == ("chat", 0)
    assert count(ctx) == 1                                            # a bare number is not an entity
