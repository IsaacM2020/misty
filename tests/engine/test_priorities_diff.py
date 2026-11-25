from misty_engine.wiki.priorities import DiffLine, Line, apply_diff, diff


def L(i, t):
    return Line(i, t)


def test_diff_and_partial_apply():
    cur = [L("p1", "Ship iGEM wiki"), L("p2", "NOI prep"), L("p3", "Cricket model")]
    proposed = ["Ship iGEM wiki", "Cricket model", "IB summer reading"]
    d = diff(cur, proposed)
    ops = {(x.op, x.text) for x in d}
    assert ops == {("remove", "NOI prep"), ("add", "IB summer reading")}
    out = apply_diff(cur, d, accepted_ids=[x.id for x in d if x.op == "add"], proposed=proposed)
    assert [l.text for l in out] == ["Ship iGEM wiki", "NOI prep", "Cricket model", "IB summer reading"]


def test_apply_everything_and_nothing():
    cur = [L("p1", "A"), L("p2", "B")]
    proposed = ["A", "C"]
    d = diff(cur, proposed)
    assert [l.text for l in apply_diff(cur, d, [x.id for x in d], proposed)] == ["A", "C"]
    assert [l.text for l in apply_diff(cur, d, [], proposed)] == ["A", "B"]


def test_diff_ids_are_stable_and_prefixed():
    d1 = diff([L("p1", "A")], ["B"])
    d2 = diff([L("p9", "A")], ["B"])
    assert [x.id for x in d1] == [x.id for x in d2] and all(x.id.startswith("d_") for x in d1)
