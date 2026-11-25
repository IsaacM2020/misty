import pytest

from misty_engine.registry import sweep as S
from misty_engine.registry.calibrate import GridRow, calibrate, choose, measure
from test_rebuild import WORLD, world  # noqa: F401  (the synthetic Pranav / Janaki / SAT world)

DUPS = {
    "groups": [
        {"name": "pranav", "members": ["Pranav Asrani", "Pranav Asrani Real", "Pranav Naveen Asrani", "Pranav"]},
        {"name": "janaki", "members": ["Janaki Kota", "Ms Janaki"]},
        {"name": "sat", "kind": "topic", "members": ["SAT Prep", "SAT Preparation"]},
    ],
    "apart": [["Pranav Asrani", "Pranav Kumar"], ["Naveen Asrani", "Pranav Asrani"]],
}


@pytest.mark.asyncio
async def test_a_sensible_point_is_complete_with_no_negative_merges(world):
    ctx, _ = world
    rows = await calibrate(ctx, DUPS, {"auto_cos": [0.92], "judge_cos": [0.3], "auto_jaccard": [0.5]})
    assert len(rows) == 1
    r = rows[0]
    assert r.negative_merges == 0 and r.golden_complete_pct == 100.0 and r.judge_pairs_needed > 0


@pytest.mark.asyncio
async def test_a_deliberately_loose_point_merges_something_it_should_not(world):
    ctx, _ = world
    # near-everything counts as "the same words": the two Pranavs and the two Asranis are not protected by names
    # alone, but conflicting full names are, so the loose point shows up on the topic side instead.
    rows = await calibrate(ctx, DUPS, {"auto_cos": [0.0], "judge_cos": [0.0], "auto_jaccard": [0.0]})
    loose = rows[0]
    assert loose.auto_merges > 0
    tight = (await calibrate(ctx, DUPS, {"auto_cos": [0.99], "judge_cos": [0.3], "auto_jaccard": [0.5]}))[0]
    assert loose.auto_merges > tight.auto_merges


@pytest.mark.asyncio
async def test_the_grid_leaves_no_thresholds_or_rows_behind(world):
    ctx, _ = world
    before = ctx.con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    await calibrate(ctx, DUPS, {"auto_cos": [0.9, 0.95], "judge_cos": [0.3, 0.5], "auto_jaccard": [0.5]})
    assert S.OVERRIDES == {} and ctx.con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == before


def test_choose_prefers_no_negatives_then_fewest_pairs_then_higher_auto_cos():
    rows = [GridRow(0.88, 0.8, 0.5, 100, 0, 900, 10), GridRow(0.92, 0.8, 0.5, 100, 0, 900, 8),
            GridRow(0.94, 0.75, 0.5, 100, 1, 100, 5), GridRow(0.90, 0.8, 0.5, 80, 0, 50, 3)]
    assert choose(rows) == rows[1]
    assert choose([rows[2]]) is None


def test_measure_counts_a_negative_when_an_apart_pair_is_one_entity(world):
    from misty_engine.registry.rebuild import import_existing
    from misty_engine.registry.store import Registry
    ctx, _ = world
    import_existing(ctx)
    apart = {"groups": [], "apart": [["Pranav Asrani", "Pranav Kumar"]]}
    assert measure(ctx, apart) == (100.0, 0)
    reg = Registry(ctx.con, ctx.clock)
    a = reg.find_alias("person", "pranav asrani")
    k = reg.find_alias("person", "pranav kumar")[0]
    reg.merge(a[0], k, reason="test")                       # a wrong merge, forced
    assert measure(ctx, apart)[1] == 1


def test_a_golden_group_the_data_contradicts_is_counted_not_held_against_the_thresholds(world):
    from misty_engine.registry.calibrate import measure_full
    from misty_engine.registry.rebuild import import_existing
    ctx, _ = world
    import_existing(ctx)
    # "Pranav Kumar" is a different person with a conflicting full name: a group that lists him is contradicted
    dups = {"groups": [{"name": "pranav", "members": ["Pranav Asrani", "Pranav Naveen Asrani", "Pranav Kumar"]}],
            "apart": []}
    pct, neg, contradicted = measure_full(ctx, dups)
    assert contradicted == 1 and neg == 0 and pct == 100.0
