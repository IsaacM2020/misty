import json

import pytest

from misty_engine import evaljudge as E
from misty_engine.errors import CapHit
from misty_engine.evalset import Case
from misty_engine.registry import render
from test_render_lint import world  # noqa: F401


def scripted(ctx, *answers):
    from fakes import ScriptedRunner
    ctx.runner = ScriptedRunner(*answers)
    return ctx.runner


def grade_reply(n):
    return {"score": n, "why": "x"}


@pytest.mark.asyncio
async def test_grade_returns_the_scripted_score_and_bad_scores_are_zero(world):
    r = scripted(world.ctx, grade_reply(2), {"score": 7}, {"nonsense": True})
    assert await E.grade(world.ctx, "q", "ref", "a", "eval-1") == 2
    assert await E.grade(world.ctx, "q", "ref", "a", "eval-1") == 0
    assert await E.grade(world.ctx, "q", "ref", "a", "eval-1") == 0
    c = r.calls[0]
    assert c["purpose"] == "eval" and c["build_id"] == "eval-1" and c["phase"] == "build"


@pytest.mark.asyncio
async def test_loss_and_lift_arithmetic(world):
    ctx = world.ctx
    i, _ = world.item("it_ev0000000000a1", summary="The wiki sync is on Friday.")
    e = world.entity("topic", "Wiki sync", [i], summary="The weekly wiki sync, on Fridays.", path="topics/wiki-sync.md")
    render.write_entity(ctx, e)
    cases = [Case("a1", "ask", "when is the wiki sync", reference="Friday"),
             Case("a2", "ask", "who runs the wiki sync", reference="Aarav")]
    # per case: answer prod, grade prod, answer cheap, grade cheap, answer search-only, grade search-only
    script = []
    for prod, cheap, so in ((2, 2, 1), (2, 1, 0)):
        script += [{"answer": "a"}, grade_reply(prod), {"answer": "b"}, grade_reply(cheap),
                   {"answer": "c"}, grade_reply(so)]
    scripted(ctx, *script)
    rep = E.EvalReport()
    await E.answers_suite(ctx, cases, rep, "eval-2")
    assert (rep.score_prod, rep.score_cheap, rep.score_search_only) == (2.0, 1.5, 0.5)
    assert rep.loss == pytest.approx(0.25) and rep.lift == pytest.approx(1.5)
    assert [c["model_key"] for c in ctx.runner.calls[:6]] == ["haiku", "haiku", "cheap", "haiku", "haiku", "haiku"]


@pytest.mark.asyncio
async def test_a_spent_eval_cap_ends_the_run_as_incomplete_not_a_failure(world):
    ctx = world.ctx
    r = scripted(ctx)
    r.raise_once(CapHit("CAP_PURPOSE:eval"))
    rep = await E.run_suites(ctx, [Case("a1", "ask", "q", reference="r")], ("answers",))
    assert rep.incomplete and "budget" in rep.why
    assert "INCOMPLETE" in E.format_report(rep)


def test_retrieval_and_pack_measure_hits_duplicates_and_precision(world):
    ctx = world.ctx
    i1, _ = world.item("it_ev0000000000b1", dated=[("2026-10-10", "kickoff")])
    i2, _ = world.item("it_ev0000000000b2", dated=[("2026-10-11", "sync")])
    igem = world.entity("project", "iGEM wiki", [i1, i2], summary="The team wiki for the iGEM competition.",
                        path="projects/igem-wiki.md")
    render.write_entity(ctx, igem)
    cases = [Case("s1", "search", "iGEM wiki", expect_entities=["projects/igem-wiki"]),
             Case("s2", "pack", "nothing matches this at all zzzz", expect_entities=["people/nobody"])]
    rep = E.EvalReport()
    E.retrieval_and_pack(ctx, cases, rep)
    assert rep.n_retrieval == 2 and rep.top5 == 0.5 and rep.misses == ["s2"]
    assert rep.pack_dup_rate == 0.0 and rep.pack_precision == 1.0


@pytest.mark.asyncio
async def test_a_private_chat_never_reaches_an_answer_prompt(world):
    ctx = world.ctx
    pub, _ = world.item("it_ev0000000000c1", summary="Public sentence about the exam.", chat_id="c1",
                        source="whatsapp")
    pub2, _ = world.item("it_ev0000000000c2", summary="Another public line about the exam.", chat_id="c1",
                         source="whatsapp")
    prv, _ = world.item("it_ev0000000000c3", summary="SECRET-LINE about the exam surprise.", chat_id="cp",
                        source="whatsapp", title="Exam surprise chat")
    e = world.entity("topic", "Exam", [pub, pub2, prv], summary="About the exam.", path="topics/exam.md")
    render.write_entity(ctx, e)
    ctx.settings.set("context_private_chats", ["whatsapp:cp"])
    r = scripted(ctx, *([{"answer": "a"}, grade_reply(2)] * 3))
    await E.run_suites(ctx, [Case("a1", "ask", "exam", reference="r")], ("answers",), cheap=False)
    assert r.calls and all("SECRET-LINE" not in " ".join(c["parts"]) for c in r.calls)


def test_the_eval_purpose_has_a_build_sub_cap_of_about_a_quarter_dollar(world):
    cap = world.ctx.spend.purpose_cap("eval", "build")
    assert cap is not None and cap[1] == pytest.approx(0.249, abs=1e-6)


def _n_entities(world, n):
    from misty_engine.registry import render
    ids = []
    for i in range(n):
        items = [world.item(f"it_sg{i:04d}{j:08d}")[0] for j in range(2)]
        e = world.entity("topic", f"Thing {i}", items, summary="Old summary.", path=f"topics/thing-{i}.md")
        world.ctx.con.execute("UPDATE entities SET importance=? WHERE id=?", (1 + i % 5, e))
        ids.append(e)
    return ids


@pytest.mark.asyncio
@pytest.mark.parametrize("wins,passed", [(14, True), (13, False)])
async def test_summary_gate_passes_at_14_of_20_and_fails_at_13(world, wins, passed):
    ids = _n_entities(world, 20)
    ctx = world.ctx
    script, k = [], 0
    for eid in ids:
        script += [{"entities": [{"id": eid, "summary": "HAIKU-TEXT summary.", "now": []}]},
                   {"entities": [{"id": eid, "summary": "STRONG-TEXT summary.", "now": []}]}]
        # Haiku wins or ties in `wins` of 20: the judge answers "tie" for those, "the strong one" for the rest
        script.append(lambda p, tie=(k < wins): {"winner": "tie" if tie else "STRONG"})
        k += 1
    runner = scripted(ctx, *script)
    # make "STRONG" resolve to whichever slot holds the strong summary: read it back from the prompt order
    orig = runner.run

    async def run(system, parts, schema, **kw):
        res = await orig(system, parts, schema, **kw)
        if schema == "eval_pair" and res.json["winner"] == "STRONG":
            body = parts[0]
            a = body.split("Summary A:")[1].split("Summary B:")[0]
            res.json["winner"] = "A" if "STRONG-TEXT" in a else "B"
        return res
    runner.run = run
    gate = await E.summaries_gate(ctx, ids)
    assert gate.n == 20 and gate.passed is passed
    assert gate.setting == ({} if passed else {"summary_strong_top_pct": 30})


@pytest.mark.asyncio
async def test_strong_model_is_used_for_the_top_share_by_rank(world):
    from misty_engine.wiki import summaries
    ids = _n_entities(world, 10)
    ctx = world.ctx
    ctx.settings.set("summary_strong_top_pct", 30)
    strong = summaries.strong_ids(ctx, ids)
    assert 3 <= len(strong) <= 4
    assert all(world.ctx.con.execute("SELECT importance FROM entities WHERE id=?", (e,)).fetchone()[0] >= 4
               for e in strong)
    ctx.settings.set("summary_strong_top_pct", 0)
    assert summaries.strong_ids(ctx, ids) == set()


def test_sample_is_stratified_and_only_two_source_entities(world):
    ids = _n_entities(world, 12)
    lone = world.entity("topic", "Lone", [world.item("it_sg9999999999")[0]], summary="x", path="topics/lone.md")
    s = E.sample_entities(world.ctx, 8)
    assert len(s) == 8 and lone not in s
    imps = [world.ctx.con.execute("SELECT importance FROM entities WHERE id=?", (e,)).fetchone()[0] for e in s]
    assert len(set(imps)) == 5                                  # every importance level appears


def test_golden_old_address_counts_as_the_survivor(world):
    """The context contract: a merged page's old address leads to the survivor. A golden case written before a
    merge names the old address; returning the survivor is a hit, not a miss."""
    ctx = world.ctx
    i1, _ = world.item("it_ev0000000000d1", dated=[("2026-10-10", "kickoff")])
    i2, _ = world.item("it_ev0000000000d2", dated=[("2026-10-11", "sync")])
    surv = world.entity("project", "iGEM", [i1, i2], summary="The iGEM competition and its team wiki.",
                        path="projects/igem-2026.md")
    render.write_entity(ctx, surv)
    ctx.con.execute("INSERT INTO entity_redirects(old_path, entity_id) VALUES (?,?)", ("projects/igem-wiki.md", surv))
    rep = E.EvalReport()
    E.retrieval_and_pack(ctx, [Case("g1", "search", "iGEM", expect_entities=["projects/igem-wiki"])], rep)
    assert rep.misses == [] and rep.top5 == 1.0 and rep.pack_precision == 1.0


@pytest.mark.asyncio
async def test_a_model_reply_that_is_not_json_scores_zero_instead_of_crashing_the_run(world):
    """A cheap model that cannot follow the JSON format fails that answer; it must not abort the whole eval."""
    from misty_engine.errors import Retryable
    r = scripted(world.ctx)
    r.raise_once(Retryable("JSON_PARSE"))
    assert await E.answer(world.ctx, "q", "notes", "cheap", "eval-1") == ""
    r.raise_once(Retryable("JSON_PARSE"))
    assert await E.grade(world.ctx, "q", "ref", "a", "eval-1") == 0


@pytest.mark.asyncio
async def test_other_retryable_errors_still_propagate_from_the_eval(world):
    from misty_engine.errors import Retryable
    r = scripted(world.ctx)
    r.raise_once(Retryable("HTTP_503"))
    with pytest.raises(Retryable):
        await E.answer(world.ctx, "q", "notes", "cheap", "eval-1")
