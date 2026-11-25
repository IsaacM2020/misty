def test_pinned_has_all_model_keys(pinned):
    m, p = pinned.models, pinned.prices
    assert m.strong and m.answer_cheap and m.strong_provider_order and m.cheap_provider_order
    assert p.strong_in > 0 and p.strong_out > 0 and p.cheap_in > 0 and p.cheap_out > 0
