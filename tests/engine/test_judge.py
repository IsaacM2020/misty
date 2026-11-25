import pytest

from misty_engine.registry.judge import JUDGE_VERSION, Question, Side, judge_pairs, pair_key


def side(key, name, n=1, kind="person", line="", aliases=()):
    return Side(key=key, kind=kind, name=name, aliases=tuple(aliases), line=line, n=n)


@pytest.mark.asyncio
async def test_cached_pair_not_asked_twice(ctx_runner):
    ctx, runner = ctx_runner({"pairs": [{"id": 1, "verdict": "same", "why": "first name + full name"}]})
    q = Question(side("e1", "Pranav"), side("e2", "Pranav Asrani"))
    v1 = await judge_pairs(ctx, [q])
    v2 = await judge_pairs(ctx, [q])
    assert v1[pair_key(q.a, q.b)].verdict == "same" and v2[pair_key(q.a, q.b)].cached
    assert len(runner.calls) == 1 and runner.calls[0]["purpose"] == "judge"


@pytest.mark.asyncio
async def test_pair_key_is_symmetric(ctx_runner):
    a, b = side("e1", "A"), side("e2", "B")
    assert pair_key(a, b) == pair_key(b, a)


@pytest.mark.asyncio
async def test_batches_of_20(ctx_runner):
    qs = [Question(side(f"a{i}", f"A{i}"), side(f"b{i}", f"B{i}")) for i in range(45)]
    ok = lambda prompt: {"pairs": [{"id": k, "verdict": "different", "why": "x"} for k in range(1, 21)]}  # noqa
    ctx, runner = ctx_runner(ok, ok, ok)
    out = await judge_pairs(ctx, qs)
    assert [c["purpose"] for c in runner.calls] == ["judge"] * 3 and len(out) == 45


@pytest.mark.asyncio
async def test_unsure_retried_only_after_growth(ctx_runner):
    ctx, runner = ctx_runner({"pairs": [{"id": 1, "verdict": "unsure", "why": "no evidence"}]},
                             {"pairs": [{"id": 1, "verdict": "same", "why": "now clear"}]})
    q = Question(side("a", "Rahul", n=2), side("b", "Rahul Mehta", n=2))
    assert (await judge_pairs(ctx, [q]))[pair_key(q.a, q.b)].verdict == "unsure"
    q4 = Question(side("a", "Rahul", n=4), side("b", "Rahul Mehta", n=2))     # grew by 2: still cached
    assert (await judge_pairs(ctx, [q4]))[pair_key(q4.a, q4.b)].cached
    q5 = Question(side("a", "Rahul", n=5), side("b", "Rahul Mehta", n=2))     # grew by 3: asked again
    assert (await judge_pairs(ctx, [q5]))[pair_key(q5.a, q5.b)].verdict == "same"
    assert len(runner.calls) == 2


@pytest.mark.asyncio
async def test_bad_json_verdict_defaults_to_unsure(ctx_runner):
    ctx, _ = ctx_runner({"pairs": [{"id": 1, "verdict": "maybe"}]})
    v = await judge_pairs(ctx, [Question(side("a", "X"), side("b", "Y"))])
    assert next(iter(v.values())).verdict == "unsure"


@pytest.mark.asyncio
async def test_cache_only_never_calls_the_model(ctx_runner):
    ctx, runner = ctx_runner({"pairs": [{"id": 1, "verdict": "same", "why": "x"}]})
    q = Question(side("a", "X1"), side("b", "X2"))
    assert await judge_pairs(ctx, [q], cache_only=True) == {}
    assert runner.calls == []
    await judge_pairs(ctx, [q])
    assert (await judge_pairs(ctx, [q], cache_only=True))[pair_key(q.a, q.b)].cached


@pytest.mark.asyncio
async def test_prompt_carries_names_not_ids_and_is_one_user_part(ctx_runner):
    ctx, runner = ctx_runner({"pairs": [{"id": 1, "verdict": "different", "why": "x"}]})
    await judge_pairs(ctx, [Question(side("en_1", "Rahul Mehta", line="classmate"), side("m:person:rahul", "Rahul"))])
    prompt = runner.calls[0]["parts"][0]
    assert "Rahul Mehta" in prompt and "classmate" in prompt and "en_1" not in prompt
    assert JUDGE_VERSION >= 1
