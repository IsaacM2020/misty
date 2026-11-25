"""hands.call is a dev probe: read-only ops only (G0 needs read_window_text)."""
from misty_engine.stream import api


def test_probe_allows_read_window_text():
    assert "read_window_text" in api.PROBE_OPS


def test_probe_has_no_write_ops():
    for op in ("menu_press", "set_minimised", "click", "type_text", "setting_apply", "open_app"):
        assert op not in api.PROBE_OPS

