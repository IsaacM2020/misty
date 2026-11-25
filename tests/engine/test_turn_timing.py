"""Season 3 Day 1: the voice-turn timing line is numbers only and never breaks a turn."""
from misty_engine.turn_timing import Marks


def test_line_has_turn_and_marks(tmp_path):
    m = Marks()
    m.mark("recv")
    m.mark("r0_first_token")
    m.mark("bad name with spaces")        # refused: names are identifiers only
    line = m.line("v1a2b3c4d")
    assert line.startswith("ENGINE_TIMING turn=v1a2b3c4d t0=")
    assert " recv=" in line and " r0_first_token=" in line and "spaces" not in line


def test_write_appends_and_survives_a_bad_path(tmp_path):
    m = Marks()
    m.mark("end")
    f = tmp_path / "voice.log"
    m.write(f, "v1")
    m.write(f, "v2")
    assert len(f.read_text().splitlines()) == 2
    m.write(tmp_path / "nope" / "voice.log", "v3")       # missing folder: swallowed
    m.write(f, "bad turn id!")                            # not an identifier: nothing written
    assert len(f.read_text().splitlines()) == 2
