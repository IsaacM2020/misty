from pathlib import Path

import pytest

from misty_engine import evalset
from misty_engine.errors import Fatal

EXAMPLE = Path(__file__).parents[1] / "golden.example.yaml"


def write(tmp_path, text):
    p = tmp_path / "g.yaml"
    p.write_text(text)
    return p


def test_example_loads():
    cases = evalset.load(EXAMPLE)
    assert [c.id for c in cases] == ["g01", "g02", "g03", "g04", "g05", "g06"] and cases[1].kind == "ask"
    assert cases[3].expect_entities and cases[4].reference and cases[5].kind == "pack"


def test_loader_rejects_duplicate_ids_and_empty_expectations(tmp_path):
    with pytest.raises(Fatal):
        evalset.load(write(tmp_path, "- {id: a, kind: search, q: x, expect_any: [y]}\n"
                                     "- {id: a, kind: search, q: z, expect_any: [y]}\n"))
    with pytest.raises(Fatal):
        evalset.load(write(tmp_path, "- {id: a, kind: search, q: x}\n"))
    with pytest.raises(Fatal):
        evalset.load(write(tmp_path, "- {id: a, kind: weird, q: x, expect_any: [y]}\n"))


async def test_runner_computes_top5_rate_and_misses(fctx, seed):
    seed("it_g1" + "0" * 14, "pendulum experiment write up", title="physics-ia.docx")
    seed("it_g2" + "0" * 14, "igem wiki parts documentation", title="igem-parts.md")
    seed("it_g3" + "0" * 14, "cubing OLL PLL algorithm sheet", title="cubing-oll.pdf")
    cases = [evalset.Case("a", "search", "physics IA pendulum", ["physics-ia"]),
             evalset.Case("b", "search", "igem parts documentation", ["igem-parts"]),
             evalset.Case("c", "search", "cubing OLL algorithms", ["oll"]),
             evalset.Case("d", "search", "tax return 2025", ["tax-return"])]
    rep = await evalset.run(fctx, cases)
    assert rep.top5_rate == pytest.approx(0.75) and rep.misses == ["d"]
    assert rep.n_search == 4 and rep.p50_ms >= 0
    assert "75%" in evalset.format_report(rep)


async def test_ask_cases_check_citations(fctx, fake_or, seed):
    from test_context_pack import put
    put(fctx, "projects/igem", "project", "iGEM", "iGEM wiki.", importance=4)
    put(fctx, "areas/school", "area", "School", "School.", importance=4)
    fake_or.chat_text("See [[projects/igem]] and [[areas/school]]: the wiki deadline is Friday.")
    cases = [evalset.Case("a", "ask", "igem?", expect_cites_any=["projects/igem"], expect_answer_any=["deadline"]),
             evalset.Case("b", "ask", "igem 2?", expect_cites_any=["people/nobody"])]
    rep = await evalset.run(fctx, cases)
    assert rep.n_ask == 2 and rep.ask_rate == 0.5 and rep.misses == ["b"]


def test_load_accepts_pack_and_reference(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("- {id: a, kind: pack, q: 'x', expect_entities: ['projects/igem']}\n"
                 "- {id: b, kind: ask, q: 'y', reference: 'ref'}\n")
    cases = evalset.load(p)
    assert cases[0].expect_entities == ["projects/igem"] and cases[1].reference == "ref"


def test_load_pack_needs_entities_and_search_accepts_entities(tmp_path):
    with pytest.raises(Fatal):
        evalset.load(write(tmp_path, "- {id: a, kind: pack, q: x}\n"))
    ok = evalset.load(write(tmp_path, "- {id: a, kind: search, q: x, expect_entities: [people/a], "
                                      "must_not_cite: [people/b]}\n"))
    assert ok[0].must_not_cite == ["people/b"]


async def test_runner_skips_pack_cases(fctx):
    rep = await evalset.run(fctx, [evalset.Case("p", "pack", "x", expect_entities=["projects/igem"])])
    assert rep.n_search == 0 and rep.n_ask == 0 and rep.misses == []


async def test_runner_skips_entity_only_search_cases(fctx):
    rep = await evalset.run(fctx, [evalset.Case("e", "search", "x", expect_entities=["people/a"])])
    assert rep.n_search == 0 and rep.misses == []


def test_dups_example_shape():
    import yaml
    d = yaml.safe_load((EXAMPLE.parent / "golden.dups.example.yaml").read_text())
    assert all(len(g["members"]) >= 2 for g in d["groups"]) and all(len(p) == 2 for p in d["apart"])
