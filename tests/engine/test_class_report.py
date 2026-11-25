"""`misty class-audio-report <session>`: counts only, read-only, for a class folder under class-audio/."""
import hashlib
import json
import os

from misty_engine import cli, class_audio, classes
from misty_engine.class_report import report

from test_class_notes import FRI

SECRET = "the secret transcript sentence about moles"


def _tree(root):
    """Every file under root with its bytes' hash and mtime: to prove the report changed nothing."""
    return sorted((str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                  for p in root.rglob("*") if p.is_file() and not p.name.endswith("-shm"))   # SQLite's reader marks, not data


def _class(con, clock, misty_home, *, preview=True, tracks=True):
    clock.t = FRI
    s = classes.start(con, clock, "Chem", None, None, mode="online")["id"]
    classes.add_segment(con, s, 0, 5, "mac", f"{SECRET} one two three")             # 9 words
    classes.add_segment(con, s, 5, 7, "mac", "and four more words")                  # 4
    classes.add_segment(con, s, 7, 9, "room", "wait what is a mole")                 # 5
    classes.end(con, clock, s)
    d = class_audio.audio_dir(misty_home.home, s)
    d.mkdir(parents=True)
    if tracks:
        (d / "tracks.json").write_text(json.dumps({"class": {"started": FRI + 1, "seconds": 612.4},
                                                   "isaac": {"started": FRI + 2, "seconds": 598.0}}))
        (d / "class.m4a").write_bytes(b"c")
        (d / "isaac.m4a").write_bytes(b"i")
    if preview:
        (d / "preview.json").write_text(json.dumps([
            {"t_start": 0, "t_end": 5, "source": "mac", "text": "garbled preview words here"},
            {"t_start": 5, "t_end": 7, "source": "room", "text": "wait"},
            {"t_start": 7, "t_end": 9, "source": "room", "text": "what is mole"}]))
    return s, d


def test_report_has_seconds_and_counts_per_source_and_no_transcript_text(con, clock, misty_home):
    s, d = _class(con, clock, misty_home)
    out = report(misty_home.home, s)
    assert f"class-audio-report {s}" in out
    assert "class.m4a  (Mac sound, teacher): 612.4 s (10:12)" in out and "isaac.m4a  (mic): 598.0 s (9:58)" in out
    assert "mac       2 segments      13 words" in out and "room      1 segments       5 words" in out
    assert "has run" in out and "live preview that was replaced:" in out
    assert "mac       1 segments       4 words" in out and "room      2 segments       4 words" in out
    assert "not recorded" in out                                          # the echo drop is not saved anywhere
    assert SECRET not in out and "garbled" not in out and "wait what" not in out


def test_report_writes_nothing_to_the_folder_or_the_database(con, clock, misty_home):
    s, d = _class(con, clock, misty_home)
    before, rows = _tree(misty_home.home), con.execute("SELECT COUNT(*) FROM class_segments").fetchone()[0]
    stat = os.stat(misty_home.db)
    report(misty_home.home, s)
    report(misty_home.home, s)
    assert _tree(misty_home.home) == before
    assert con.execute("SELECT COUNT(*) FROM class_segments").fetchone()[0] == rows == 3
    assert os.stat(misty_home.db).st_mtime_ns == stat.st_mtime_ns
    d.chmod(0o500)                                                        # even a read-only folder is fine
    try:
        assert report(misty_home.home, s)
    finally:
        d.chmod(0o700)


def test_report_before_the_after_class_pass_and_with_a_missing_track(con, clock, misty_home):
    s, d = _class(con, clock, misty_home, preview=False)
    (d / "isaac.m4a").unlink()
    (d / "tracks.json").write_text(json.dumps({"class": {"started": FRI, "seconds": 30}, "isaac": {"started": FRI, "seconds": 20}}))
    out = report(misty_home.home, s)
    assert "has not run (no preview.json)" in out and "isaac.m4a  (mic): 20.0 s (0:20)  [file missing]" in out
    (d / "tracks.json").write_text(json.dumps({"class": {"started": FRI, "seconds": 30}}))
    assert "isaac.m4a  (mic): not recorded" in report(misty_home.home, s)
    (d / "tracks.json").write_text("{not json")
    assert "tracks.json: missing or unreadable" in report(misty_home.home, s)


def test_report_with_a_folder_but_no_saved_class_and_the_other_way_round(con, clock, misty_home):
    d = class_audio.audio_dir(misty_home.home, "cs_nodb")
    d.mkdir(parents=True)
    (d / "tracks.json").write_text(json.dumps({"class": {"started": FRI, "seconds": 5}}))
    out = report(misty_home.home, "cs_nodb")
    assert "class.m4a  (Mac sound, teacher): 5.0 s" in out and "none saved" in out
    s, folder = _class(con, clock, misty_home)
    import shutil
    shutil.rmtree(folder)
    out = report(misty_home.home, s)
    assert "no audio folder" in out and "mac       2 segments" in out
    assert report(misty_home.home, "cs_unknown") is None


def test_report_refuses_ids_that_are_paths(misty_home):
    (misty_home.home / "secret").mkdir()
    for bad in ("../", "..", "../secret", "a/b", "", "x" * 41, "cs_1; rm"):
        assert report(misty_home.home, bad) is None


def test_cli_command_prints_the_report_and_fails_on_an_unknown_class(con, clock, misty_home, capsys):
    s, d = _class(con, clock, misty_home)
    assert cli.main(["class-audio-report", s]) == 0
    assert "612.4 s" in capsys.readouterr().out
    assert cli.main(["class-audio-report", "cs_nope"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "no such class" in captured.err
