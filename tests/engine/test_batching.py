from misty_engine.compile.batching import make_batches


def test_batches_cover_every_id_once_and_respect_limits():
    items = [(f"it_{i}", t) for i, t in enumerate([100] * 20 + [5000, 7000, 900, 1300])]
    out = make_batches(items)
    flat = [i for b in out for i in b]
    assert sorted(flat) == sorted(i for i, _ in items)
    sizes = dict(items)
    assert all(len(b) <= 8 and (len(b) == 1 or sum(sizes[i] for i in b) <= 6000) for b in out)


def test_large_items_go_alone_and_empty_input():
    assert make_batches([("a", 5000), ("b", 10)]) == [["a"], ["b"]]
    assert make_batches([]) == []
