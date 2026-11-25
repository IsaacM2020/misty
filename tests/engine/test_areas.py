import json

import numpy as np
import pytest

import personas as P
from fakes import ScriptedRunner
from misty_engine.errors import Fatal
from misty_engine.wiki import areas as A
from misty_engine.wiki.graph import build_graph
from misty_engine.wiki.notes import entity_path, item_entities, section

THEMES = ("alpha", "beta", "gamma")          # made-up themes for the plumbing tests only


def oracle(theme_paths, names=None):
    """What a perfect model would answer for a persona: one area per hidden theme."""
    return {"areas": [{"name": (names or [f"Area {i}" for i in range(len(theme_paths))])[i],
                       "description": f"Things about area {i}.", "importance": 3 - i % 3,
                       "members": sorted(paths), "evidence": "because"} for i, paths in enumerate(theme_paths)],
            "gaps": ["no messages connected"]}


# ---------------------------------------------------------------- pure pieces

def test_prompt_and_code_carry_no_example_area_names():
    banned = ["school", "ib ", "igem", "homelab", "fitness", "travel", "career", "leadership", "isaac", "college",
              "hardware", "schedule and"]
    text = A.SYS_SYNTH.lower()
    assert not [w for w in banned if w in text]


def test_digest_is_scrubbed_and_never_names_hidden_themes():
    notes, ents, about, rubric, _ = P.corpus("nurse")
    notes[0].summary = "password: hunter2 and token AKIAIOSFODNN7EXAMPLE"
    d = A.build_digest(notes, ents, about)
    assert "hunter2" not in d and "AKIAIOSFODNN7EXAMPLE" not in d
    assert "## ENTITIES" in d and "## NOTES" in d and "ABOUT THE PERSON" in d


def test_farthest_points_spreads_across_groups():
    rng = np.random.default_rng(0)
    X = np.vstack([np.eye(8)[i] * 5 + rng.normal(0, 0.1, (10, 8)) for i in range(3)])
    picked = A.farthest_points(X, 3)
    assert sorted(i // 10 for i in picked) == [0, 1, 2]


def test_parse_areas_drops_unknown_paths_duplicates_and_empty_areas():
    allowed = {"people/a", "topics/b"}
    data = {"areas": [
        {"name": "One", "description": "d", "importance": 9, "members": ["people/a", "ghost/x"]},
        {"name": "one", "description": "dup", "importance": 3, "members": ["topics/b"]},
        {"name": "Two", "description": "d", "importance": "x", "members": ["ghost/y"]},
        {"name": "Three", "description": "d", "importance": 2, "members": ["topics/b"]}], "gaps": ["g", 5]}
    areas, gaps = A.parse_areas(data, allowed)
    assert [(a.name, a.importance, a.members) for a in areas] == [("One", 5, ["people/a"]), ("Three", 2, ["topics/b"])]
    assert gaps == ["g"]
    with pytest.raises(Fatal):
        A.parse_areas({"areas": "nope"}, allowed)


def test_assign_notes_follow_entities_and_leftovers_join_nearest_area():
    areas = [A.Area("A", "d", 3, ["people/a"]), A.Area("B", "d", 3, ["people/b"])]
    notes = [A.NoteRec(f"i{k}", "t", "files", "2026-01-01", "s", [p]) for k, p in
             enumerate(["people/a", "people/a", "people/b", "people/b", "topics/x"])]
    X = np.array([[1, 0], [1, 0.1], [0, 1], [0.1, 1], [0.05, 1]], dtype=np.float32)
    cov = A.assign_notes(areas, notes, X)
    assert cov == 1.0 and set(areas[1].item_ids) == {"i2", "i3", "i4"}
    assert A.assign_notes(areas, notes, None) == 0.8                     # without vectors the leftover stays unplaced


# ---------------------------------------------------------------- synthesis on personas (fake model)

@pytest.mark.parametrize("persona", sorted(P.PERSONAS))
async def test_persona_pipeline_with_a_perfect_model_places_everything(persona):
    notes, ents, about, rubric, theme_paths = P.corpus(persona)
    runner = ScriptedRunner()
    runner.set("life_areas", oracle(theme_paths))
    out = await A.synthesize_areas(runner, notes, ents, about)
    assert len(out["areas"]) == len(theme_paths) and out["coverage"] == 1.0
    assert runner.model_keys == ["strong"] and len(runner.calls) == 1
    assert runner.calls[0]["purpose"] == "areas" and runner.calls[0]["max_tokens"] >= 6000
    assert out["gaps"] == ["no messages connected"]
    assert all(p == 1.0 for p in P.theme_purity(out["areas"], theme_paths))


async def test_synthesis_asks_again_once_when_coverage_is_low_and_says_what_is_missing():
    notes, ents, about, rubric, theme_paths = P.corpus("founder")
    runner = ScriptedRunner()
    thin = oracle(theme_paths[:3])
    full = oracle(theme_paths)
    answers = [thin, full]
    runner.set("life_areas", lambda prompt: answers.pop(0))
    out = await A.synthesize_areas(runner, notes, ents, about)
    assert len(runner.calls) == 2 and len(out["areas"]) == len(theme_paths)
    assert "Entities with no area yet" in runner.prompts[1] and "Only" in runner.prompts[1]


# ---------------------------------------------------------------- on a ctx (items, wiki, review)

def build_entity_pages(ctx, item_ids):
    """One registry entity (active, with a real summary and a page) per name the notes mention."""
    from misty_engine.registry import render
    from misty_engine.registry.store import Registry
    reg = Registry(ctx.con, ctx.clock)
    made: dict[str, str] = {}
    for iid in item_ids:
        meta = json.loads(ctx.con.execute("SELECT meta FROM items WHERE id=?", (iid,)).fetchone()["meta"])
        for kind, name in item_entities(meta):
            path = entity_path(kind, name)
            if path not in made:
                eid = reg.new_entity(kind, name, status="active", path=path, known=True)
                ctx.con.execute("INSERT INTO entity_text(entity_id, summary, summary_n) VALUES (?,?,1)",
                                (eid, f"{name}."))
                made[path] = eid
            reg.add_mention(iid, made[path], name)
    with ctx.wiki.batch("test: entity pages"):
        for eid in made.values():
            render.write_entity(ctx, eid)


@pytest.fixture
def corpus(fctx, seed):
    """24 compiled notes in 3 themes with entity pages built from their drafts."""
    ids, theme_of = [], {}
    for i in range(24):
        t = THEMES[i // 8]
        iid = f"it_{i:016x}"
        seed(iid, f"text about {t} " * 30, title=f"{t} note {i}")
        path = f"sources/folders/{iid[-6:]}.md"
        meta = {"people": [f"Person {t}"], "projects": [f"{t.title()} project"], "topics": [],
                "dated_lines": [["2026-10-01", "x"]] if i % 3 == 0 else []}
        fctx.con.execute("UPDATE items SET status='embedded', note_path=?, modified_at=?, meta=? WHERE id=?",
                         (path, "2026-09-20T00:00:00+00:00", json.dumps(meta), iid))
        ids.append(iid)
        theme_of[iid] = t
    build_entity_pages(fctx, ids)
    fctx.runner = ScriptedRunner()
    fctx.runner.set("life_areas", {"areas": [
        {"name": f"{t.title()} life", "description": f"All about {t}.", "importance": 4 - k,
         "members": [f"people/person-{t}", f"projects/{t}-project"], "evidence": "e"} for k, t in enumerate(THEMES)],
        "gaps": ["no calendar connected"]})
    return ids


async def test_discover_writes_proposed_areas_and_entities_follow(fctx, corpus):
    out = await A.discover(fctx, min_areas=2)
    assert out["areas"] == 3 and out["coverage"] == 1.0 and fctx.runner.model_keys == ["strong"]
    areas = A.read_areas(fctx)
    assert {a["status"] for a in areas} == {"proposed"} and sorted(a["notes"] for a in areas) == [8, 8, 8]
    assert fctx.con.execute("SELECT COUNT(*) FROM items WHERE area IS NOT NULL").fetchone()[0] == 24
    proj = fctx.wiki.read("projects/alpha-project.md")
    assert proj.fm["area"] == "alpha-life"
    assert "[[projects/alpha-project]]" in section(fctx.wiki.read("areas/alpha-life.md").body, "Links")
    assert fctx.settings.get("area_gaps") == ["no calendar connected"]


async def test_review_rename_merge_drop_then_accept(fctx, corpus):
    await A.discover(fctx, min_areas=2)
    assert A.rename_area(fctx, "alpha-life", "Bench Work") == "bench-work"
    A.merge_areas(fctx, "bench-work", "beta-life")
    assert fctx.wiki.read("areas/bench-work.md").fm["notes"] == 16 and not fctx.wiki.exists("areas/beta-life.md")
    A.drop_area(fctx, "gamma-life")
    assert fctx.con.execute("SELECT COUNT(*) FROM items WHERE area IS NOT NULL").fetchone()[0] == 16
    assert A.accept_all(fctx) == 1 and [a["slug"] for a in A.accepted(fctx)] == ["bench-work"]
    with pytest.raises(Fatal):                         # accepted areas are never silently replaced
        await A.discover(fctx)
    assert (await A.discover(fctx, force=True, min_areas=2))["areas"] == 3


async def test_too_few_notes_and_failed_synthesis_are_clean_errors(fctx, seed):
    with pytest.raises(Fatal) as e:
        await A.discover(fctx)
    assert e.value.code == "TOO_FEW_NOTES"


def test_plan_reports_model_and_digest_size(fctx, corpus):
    p = A.plan(fctx)
    assert p["model"] == fctx.pinned.models.strong and p["notes"] == 24 and p["digest_tokens"] > 0


async def test_graph_has_user_at_centre_all_areas_and_respects_budget(fctx, corpus):
    await A.discover(fctx, min_areas=2)
    A.accept_all(fctx)
    g = build_graph(fctx, user_label="Sam")
    assert g["nodes"][0] == {"id": "me", "label": "Sam", "type": "self", "area": None, "importance": 5,
                             "glow": 0.0, "last_date": None, "sources": 0}
    assert [n["type"] for n in g["nodes"]].count("area") == 3 and len(g["nodes"]) <= 60
    area_ids = {n["id"] for n in g["nodes"] if n["type"] == "area"}
    assert {e["target"] for e in g["edges"] if e["source"] == "me"} == area_ids


# ---------------------------------------------------------------- the real model on made-up people (Pro quota)

@pytest.mark.live
@pytest.mark.parametrize("persona", sorted(P.PERSONAS))
async def test_live_persona_areas_with_the_real_strong_model(persona, capsys, con, settings, clock, pinned):
    """One real strong-model call per persona (about $0.1 each). Needs OPENROUTER_API_KEY in the environment."""
    import os
    from misty_engine.net import NetState
    from misty_engine.openrouter import OpenRouterClient, user_approved
    from misty_engine.runner import ApiRunner
    from misty_engine.spend import SpendMeter
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("OPENROUTER_API_KEY not set")
    settings.set("phase", "build")
    settings.set("cap_build_usd", 1.0)
    client = OpenRouterClient(pinned=pinned, settings=settings, spend=SpendMeter(con, clock, settings),
                              net=NetState(), key_provider=lambda: key, clock=clock)
    notes, ents, about, rubric, theme_paths = P.corpus(persona)
    with user_approved():
        out = await A.synthesize_areas(ApiRunner(client, lambda: "build"), notes, ents, about)
    hits, purity = P.rubric_hits(out["areas"], rubric), P.theme_purity(out["areas"], theme_paths)
    with capsys.disabled():
        print(f"\n== {persona}: {len(out['areas'])} areas, coverage {out['coverage']}, "
              f"rubric {sum(hits)}/{len(hits)}, purity {round(sum(purity) / len(purity), 2)}")
        for a in out["areas"]:
            print(f"   [{a.importance}] {a.name}: {a.description}")
        for g in out["gaps"]:
            print(f"   gap: {g}")
    names = [a.name.lower() for a in out["areas"]]
    assert A.MIN_AREAS <= len(out["areas"]) <= A.MAX_AREAS and len(set(names)) == len(names)
    assert out["coverage"] >= A.MIN_COVERAGE
    assert sum(hits) / len(hits) >= 0.7
    assert sum(purity) / len(purity) >= 0.6


async def test_assign_area_picks_the_majority_and_none_when_no_area_has_notes(fctx, corpus):
    from misty_engine.registry.store import Registry
    await A.discover(fctx, min_areas=2)
    reg = Registry(fctx.con, fctx.clock)
    alpha = reg.find_alias("project", "alpha project")[0]
    assert A.assign_area(fctx, alpha) == "alpha-life"
    stray = reg.new_entity("topic", "Stray Thing", status="active", path="topics/stray-thing.md")
    fctx.con.execute("INSERT INTO items(id, source, kind, locator, title, status, meta, updated_at) "
                     "VALUES ('it_stray0000000001','folders','file','/s','t','done','{}',0)")
    reg.add_mention("it_stray0000000001", stray, "Stray Thing")
    assert A.assign_area(fctx, stray) is None                       # a note in no area, no vector: no area


async def test_a_page_mirrors_the_registry_area_after_assignment(fctx, corpus):
    await A.discover(fctx, min_areas=2)
    assert fctx.wiki.read("projects/beta-project.md").fm["area"] == "beta-life"
    assert fctx.con.execute("SELECT area FROM entities WHERE name='Beta project'").fetchone()[0] == "beta-life"
