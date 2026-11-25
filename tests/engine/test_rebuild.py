import json
import re

import pytest

from misty_engine.registry import rebuild as RB
from misty_engine.registry.rebuild import find_classes, import_existing, rebuild

NOTE = ("---\ntype: source\ntitle: {t}\nitem_id: {i}\nsource: gmail\nupdated: 2026-10-14\n---\n\n"
        "## Summary\n{s}\n\n## Key facts\n- f\n\n## People\n- (none)\n\n## Links\n- (none)\n")

# kind, title, summary (None = stub), how many notes name it
WORLD = [
    ("person", "Pranav Asrani", "Friend from school.", 4),
    ("person", "Pranav Asrani Real", None, 2),
    ("person", "Pranav Naveen Asrani", None, 2),
    ("person", "Pranav", None, 2),
    ("person", "Pranav Kumar", "Cousin from Chennai.", 3),          # negative: not Pranav Asrani
    ("person", "Naveen Asrani", "Pranav's father.", 3),             # negative: not Pranav Asrani
    ("person", "Janaki Kota", "Chemistry teacher.", 3),
    ("person", "Ms Janaki", None, 2),
    ("person", "MC SA2 OVER chat member", None, 1),                 # a chat name, never a person
    ("topic", "SAT Prep", "Preparing for the SAT exam.", 3),
    ("topic", "SAT Preparation", None, 2, "Preparing for the SAT exam."),
    ("topic", "Cricket", "The sport.", 3),
]


def judge(prompt):
    """A model that says same for every pair except those that mention Kumar."""
    pairs = []
    for m in re.finditer(r"(\d+)\.\n   A: (.*)\n   B: (.*)", prompt):
        n, a, b = int(m.group(1)), m.group(2), m.group(3)
        pairs.append({"id": n, "verdict": "different" if "Kumar" in a + b else "same", "why": "x"})
    return {"pairs": pairs}


@pytest.fixture
def world(ctx_runner, monkeypatch):
    # the char-trigram fake embedder is far less semantic than the real one: lower the band for these tests
    monkeypatch.setattr("misty_engine.registry.sweep.JUDGE_COS", 0.3)
    ctx, runner = ctx_runner(*[judge] * 12)
    n = 0
    with ctx.wiki.batch("test world"):
        for kind, title, summary, notes, *rest in WORLD:
            folder = {"person": "people", "project": "projects", "topic": "topics"}[kind]
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            iids = []
            for _ in range(notes):
                n += 1
                iid = f"it_{n:016x}"
                npath = f"sources/gmail/{iid[-6:]}.md"
                (ctx.paths.wiki / npath).parent.mkdir(parents=True, exist_ok=True)
                (ctx.paths.wiki / npath).write_text(NOTE.format(t=f"Note {n}", i=iid, s=(rest[0] if rest else f"About {title}.")))
                key = {"person": "people", "project": "projects", "topic": "topics"}[kind]
                ctx.con.execute(
                    "INSERT INTO items(id, source, kind, locator, title, status, note_path, modified_at, meta, "
                    "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (iid, "gmail", "mail_thread", f"/x/{iid}", f"Note {n}", "done", npath, "2026-10-12T00:00:00+00:00",
                     json.dumps({key: [title], "dated_lines": [["2026-10-1%d" % (n % 9), f"{title} thing"]]}),
                     ctx.clock.now()))
                iids.append(iid)
            fm = {"type": kind, "title": title, "aliases": [], "area": None, "importance": 3,
                  "updated": "2026-10-14", "sources": iids}
            body = f"## Summary\n{summary or '(stub)'}\n\n## Now\n\n## Timeline\n\n## Links\n"
            ctx.wiki.write(f"{folder}/{slug}.md", fm, body)
            text = f"{title} {summary or ''}"
            ctx.index.upsert_page(f"{folder}/{slug}.md", text, ctx.embedder.embed_documents([text])[0])
    return ctx, runner


def names(ctx, kind, statuses=("active", "candidate")):
    q = f"SELECT name FROM entities WHERE kind=? AND status IN ({','.join('?' * len(statuses))}) ORDER BY name"
    return [r[0] for r in ctx.con.execute(q, (kind, *statuses))]


def test_import_keeps_real_summaries_and_turns_stubs_into_candidates(world):
    ctx, _ = world
    out = import_existing(ctx)
    assert out["active"] == 6 and out["candidate"] == 5 and out["rejected"] == {"CHAT_WORD": 1}
    r = ctx.con.execute("SELECT e.status, t.summary FROM entities e LEFT JOIN entity_text t ON t.entity_id = e.id "
                        "WHERE e.name='Pranav Asrani'").fetchone()
    assert r["status"] == "active" and r["summary"] == "Friend from school."
    stub = ctx.con.execute("SELECT id, status, path FROM entities WHERE name='Pranav Asrani Real'").fetchone()
    assert stub["status"] == "candidate" and stub["path"] is None
    assert ctx.con.execute("SELECT entity_id FROM entity_redirects WHERE old_path='people/pranav-asrani-real.md'"
                           ).fetchone()[0] == stub["id"]
    assert import_existing(ctx)["already"] == 11                    # safe to run twice


@pytest.mark.asyncio
async def test_rebuild_merges_groups_and_keeps_negatives(world):
    ctx, runner = world
    rep = await rebuild(ctx, dry_run=False, cache_only=False)
    # the survivor is named by the longest clean alias (spec 3.1), so the Pranav group is its full spelling
    assert names(ctx, "person") == ["Janaki Kota", "Naveen Asrani", "Pranav Kumar", "Pranav Naveen Asrani"]
    topics = names(ctx, "topic")
    assert len([n for n in topics if "SAT" in n]) == 1 and "Cricket" in topics
    assert rep.merges and rep.pending == 0
    # the winner keeps the real summary and every spelling is an alias or a redirect
    pa = ctx.con.execute("SELECT id FROM entities WHERE name='Pranav Naveen Asrani' AND status='active'").fetchone()[0]
    aliases = {r[0] for r in ctx.con.execute("SELECT alias_norm FROM entity_aliases WHERE entity_id=?", (pa,))}
    assert {"pranav asrani", "pranav naveen asrani", "pranav"} <= aliases
    assert ctx.con.execute("SELECT entity_id FROM entity_redirects WHERE old_path='people/pranav.md'").fetchone()
    # no mention was lost: every named person mention of every note is attached or plain
    assert ctx.con.execute("SELECT COUNT(*) FROM mentions").fetchone()[0] >= 20


@pytest.mark.asyncio
async def test_rebuild_never_merges_the_apart_pairs_even_if_the_judge_says_same(world):
    ctx, _ = world
    await rebuild(ctx, dry_run=False, cache_only=False)
    reg = ctx.con
    def entity_of(alias_norm):
        return {r[0] for r in reg.execute(
            "SELECT entity_id FROM entity_aliases a JOIN entities e ON e.id = a.entity_id "
            "WHERE a.alias_norm=? AND e.status != 'merged'", (alias_norm,))}
    assert entity_of("naveen asrani").isdisjoint(entity_of("pranav asrani"))
    assert entity_of("pranav kumar").isdisjoint(entity_of("pranav asrani"))


@pytest.mark.asyncio
async def test_rebuild_dry_run_writes_nothing_and_calls_no_model(world):
    ctx, runner = world
    before = {t: ctx.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("entities", "entity_aliases", "mentions", "entity_merges", "judgements", "entity_redirects")}
    files = sorted(ctx.wiki.all_pages())
    head = ctx.wiki.head()
    rep = await rebuild(ctx, dry_run=True)
    assert runner.calls == []
    assert {t: ctx.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before} == before
    assert sorted(ctx.wiki.all_pages()) == files and ctx.wiki.head() == head
    x = rep.extra
    assert x["pages_before"] == 12 and x["entities_before"]["person"] >= 7
    assert x["import_rejected"] == {"CHAT_WORD": 1}
    assert rep.judge_pairs_needed > 0 and x["estimated_judge_usd"] > 0
    assert x["pages_after_if_all_open_pairs_same"] <= x["pages_after_estimate"]
    assert x["pages_after_estimate"] >= 6
    json.dumps(rep.counts())                                                 # counts only, JSON-able


def test_find_classes_groups_but_never_across_a_conflict(world):
    ctx, _ = world
    import_existing(ctx)
    classes = find_classes(ctx)
    reg_names = lambda g: sorted(ctx.con.execute("SELECT name FROM entities WHERE id=?", (e,)).fetchone()[0] for e in g)
    flat = [reg_names(g) for g in classes]
    pranav = next(g for g in flat if "Pranav Asrani" in g)
    assert "Naveen Asrani" not in pranav and "Pranav Kumar" not in pranav
    assert {"Pranav Asrani Real", "Pranav Naveen Asrani"} <= set(pranav)


@pytest.mark.asyncio
async def test_diag_registry_dry_run_is_counts_only(world):
    from misty_engine.api import build_methods
    ctx, runner = world
    out = await build_methods(ctx)["diag.registry_dry_run"]({})
    dumped = json.dumps(out)
    for _, title, *_ in WORLD:
        assert title not in dumped                                  # no names, no paths, no ids
    assert not re.search(r"en_[0-9a-f]{12}|it_[0-9a-f]{16}|\.md", dumped)
    assert out["judge_pairs_needed"] > 0 and out["extra"]["estimated_judge_usd"] > 0 and runner.calls == []
    assert ctx.con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0     # nothing stayed


def test_cli_registry_rebuild_is_wired():
    from misty_engine import cli
    p = cli.build_parser() if hasattr(cli, "build_parser") else None
    assert p is not None
    a = p.parse_args(["registry", "rebuild", "--dry-run"])
    assert a.cmd == "registry" and a.action == "rebuild" and a.dry_run is True and a.apply is False


@pytest.mark.asyncio
async def test_a_migration_build_id_puts_every_judge_call_under_build_caps(world):
    ctx, runner = world
    await rebuild(ctx, dry_run=False, cache_only=False, build_id="migrate-1")
    assert runner.calls and all(c["build_id"] == "migrate-1" and c["phase"] == "build" for c in runner.calls)


def test_cli_apply_and_summarise_take_a_hard_cap():
    from misty_engine import cli
    p = cli.build_parser()
    a = p.parse_args(["registry", "rebuild", "--apply", "--max-usd", "0.5"])
    assert a.apply and a.max_usd == 0.5
    b = p.parse_args(["onboard", "summarise", "--max-usd", "0.25"])
    assert b.action == "summarise" and b.max_usd == 0.25
    c = p.parse_args(["spend", "--by-purpose", "--since", "2026-10-15"])
    assert c.by_purpose and c.since == "2026-10-15"
