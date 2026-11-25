"""Class store: one live session at a time, segments scrubbed and searchable, gaps when the lid closes, abandoned
sessions closed, transcripts pruned after 6 months while the notes stay."""
from misty_engine import classes

DAY = 86400.0


def test_start_ends_the_other_live_session(con, clock):
    a = classes.start(con, clock, "Chem", None, None)
    clock.advance(60)
    b = classes.start(con, clock, "Math", "Math AA", None)
    assert classes.get(con, a["id"])["session"]["status"] == "ended"
    assert classes.get(con, b["id"])["session"]["status"] == "live"
    assert a["title"].startswith("Chem, ")
    assert b["title"] == "Math AA"


def test_segment_is_scrubbed_and_searchable(con, clock):
    s = classes.start(con, clock, "Chem", None, None)["id"]
    classes.add_segment(con, s, 0.0, 3.0, "room", "my key is sk-or-v1-" + "a1b2c3d4" * 8)
    classes.add_segment(con, s, 3.0, 6.0, "room", "Le Chatelier shifts equilibrium")
    assert classes.add_segment(con, s, 6.0, 7.0, "room", "   ") is None
    hits = con.execute("SELECT rowid FROM class_segments_fts WHERE class_segments_fts MATCH 'chatelier'").fetchall()
    assert len(hits) == 1
    texts = [r["text"] for r in classes.get(con, s)["segments"]]
    assert len(texts) == 2 and not any("sk-or" in t for t in texts)


def test_homework_line_survives_scrub(con, clock):
    s = classes.start(con, clock, "Chem", None, None)["id"]
    classes.add_segment(con, s, 0.0, 3.0, "room", "Q1-8 page 112, due 28 Oct")
    assert classes.get(con, s)["segments"][0]["text"] == "Q1-8 page 112, due 28 Oct"


def test_pause_and_resume_record_a_gap(con, clock):
    s = classes.start(con, clock, "Chem", None, None)["id"]
    clock.advance(600)
    classes.pause(con, clock, s, "lid")
    assert classes.get(con, s)["session"]["status"] == "paused"
    clock.advance(300)
    classes.resume(con, clock, s)
    sess = classes.get(con, s)["session"]
    assert sess["status"] == "live"
    assert sess["gaps"] == [{"from": 600.0, "to": 900.0, "reason": "lid"}]


def test_close_stale_ends_abandoned_sessions(con, clock):
    s = classes.start(con, clock, "Chem", None, None)["id"]
    classes.add_segment(con, s, 0.0, 2.0, "room", "hello class")
    clock.advance(29 * 60)
    assert classes.close_stale(con, clock) == []
    clock.advance(2 * 60)
    assert classes.close_stale(con, clock) == [s]
    assert classes.get(con, s)["session"]["status"] == "ended"


def test_prune_drops_old_segments_keeps_notes(con, clock):
    s = classes.start(con, clock, "Chem", None, None)["id"]
    classes.add_segment(con, s, 0.0, 2.0, "room", "old words")
    classes.end(con, clock, s)
    clock.advance(182 * DAY)
    assert classes.prune(con, clock) == 0
    assert len(classes.get(con, s)["segments"]) == 1
    clock.advance(2 * DAY)
    assert classes.prune(con, clock) == 1
    got = classes.get(con, s)
    assert got["segments"] == [] and got["session"]["id"] == s
    assert con.execute("SELECT count(*) FROM class_segments_fts WHERE class_segments_fts MATCH 'old'").fetchone()[0] == 0


def test_delete_removes_segments_and_actions(con, clock):
    s = classes.start(con, clock, "Chem", None, None)["id"]
    classes.add_segment(con, s, 0.0, 2.0, "room", "homework is page 5")
    con.execute("INSERT INTO class_actions(id, session_id, text, kind, due_text, seg_id) VALUES "
                "('ca_1', ?, 'page 5', 'homework', '', 1)", (s,))
    classes.delete(con, s)
    assert classes.list(con) == []
    assert con.execute("SELECT count(*) FROM class_segments").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM class_actions").fetchone()[0] == 0


def test_online_class_keeps_its_mode_and_default_is_room(con, clock):
    a = classes.start(con, clock, "Chem", None, None)
    assert a["mode"] == "room"
    b = classes.start(con, clock, "Math", None, None, mode="online")
    assert classes.get(con, b["id"])["session"]["mode"] == "online"
    import pytest
    from misty_engine.errors import Fatal
    with pytest.raises(Fatal):
        classes.start(con, clock, "Math", None, None, mode="zoom")


def test_notes_transcript_labels_teacher_and_isaac_only_online():
    from misty_engine import class_notes
    from datetime import date
    segs = [{"id": 1, "t_start": 0, "source": "mac", "text": "Today is moles"},
            {"id": 2, "t_start": 5, "source": "room", "text": "is that per litre?"}]
    online = class_notes._parts({"subject": "Chem", "title": "t", "his_notes": "", "gaps": [], "mode": "online"},
                                segs, date(2026, 10, 24))
    assert "[1] 00:00 Teacher: Today is moles" in online[1] and "[2] 00:05 Isaac: is that per litre?" in online[1]
    assert "online" in online[0]
    room = class_notes._parts({"subject": "Chem", "title": "t", "his_notes": "", "gaps": [], "mode": "room"},
                              segs, date(2026, 10, 24))
    assert "Teacher:" not in room[1] and "Isaac:" not in room[1] and "in person" in room[0]


def test_slides_are_scrubbed_deduped_capped_and_deleted_with_the_class(con, clock):
    s = classes.start(con, clock, "Chem", None, None, "online")["id"]
    assert classes.add_slide(con, s, 10, "Le Chatelier's principle: equilibrium shifts") is not None
    assert classes.add_slide(con, s, 20, "Le Chatelier's principle:   equilibrium shifts") is None      # same slide again
    assert classes.add_slide(con, s, 30, "Mute Share") is None                                         # app buttons only
    assert classes.add_slide(con, s, 40, "my key is sk-or-v1-" + "a1b2c3d4" * 8 + " and more words here") is not None
    texts = [r["text"] for r in classes.get(con, s)["slides"]]
    assert len(texts) == 2 and not any("sk-or" in t for t in texts)
    for i in range(classes.MAX_SLIDES + 5):
        classes.add_slide(con, s, 50 + i, f"slide number {i} has several words")
    assert len(classes.get(con, s)["slides"]) == classes.MAX_SLIDES
    classes.delete(con, s)
    assert con.execute("SELECT COUNT(*) FROM class_slides").fetchone()[0] == 0


def test_notes_get_the_shared_screen_spread_over_the_class():
    from misty_engine import class_notes
    from datetime import date
    sess = {"subject": "Chem", "title": "t", "his_notes": "", "gaps": [], "mode": "online"}
    slides = [{"t": i * 10, "text": f"slide {i} " + "x" * 600} for i in range(100)]
    parts = class_notes._parts(sess, [], date(2026, 10, 24), slides)
    screen = next(p for p in parts if p.startswith("On the shared screen"))
    lines = screen.splitlines()[1:]
    assert len(lines) == 30 and all(len(ln) <= 6 + 400 for ln in lines)
    assert not any(p.startswith("On the shared screen") for p in class_notes._parts(sess, [], date(2026, 10, 24)))


def test_rename_sets_title_and_subject_and_keeps_what_is_not_given(con, clock):
    s = classes.start(con, clock, "Chem", None, None)
    assert classes.is_default_title(s)
    out = classes.rename(con, s["id"], "  Organic   intro ", None)
    assert out["title"] == "Organic intro" and out["subject"] == "Chem"
    assert not classes.is_default_title(out)
    out = classes.rename(con, s["id"], None, "Physics")
    assert out["title"] == "Organic intro" and out["subject"] == "Physics"


def test_dated_puts_the_class_day_after_the_name(clock):
    assert classes.dated("Projectile motion.", 1_792_720_800.0) == "Projectile motion, 23 Oct"


# ---- merge: a class that was split in two (EDITH restarted mid-class) becomes one

def _split_class(con, clock):
    """16 Nov: the first half (teacher only, 16 min), a 3 min restart, then the second half (both tracks)."""
    a = classes.start(con, clock, "Chem", "Chem Class, 16 Nov", None, "online")["id"]
    classes.add_segment(con, a, 0.0, 5.0, "mac", "back titration is for hazy solutions")
    classes.add_segment(con, a, 900.0, 960.0, "mac", "leftover acid is titrated with NaOH")
    clock.advance(1000)
    classes.end(con, clock, a)                       # ended_at = start + 1000
    clock.advance(180)                               # restart: the second half starts 1180 s after the first
    b = classes.start(con, clock, "Chem", "Chem Class, 16 Nov", None, "online")["id"]
    classes.add_segment(con, b, 2.0, 7.0, "room", "so excess acid is the one we titrate")
    classes.add_segment(con, b, 30.0, 40.0, "mac", "atom economy uses all the reactants")
    classes.add_slide(con, b, 12.0, "Atom economy = Mr of product over Mr of reactants")
    con.execute("INSERT INTO class_actions(id, session_id, text, kind, due_text, seg_id) VALUES "
                "('ca_1', ?, 'worksheet', 'homework', '', (SELECT MIN(id) FROM class_segments WHERE session_id=?))", (b, b))
    clock.advance(600)
    classes.end(con, clock, b)
    return a, b


def test_merge_puts_both_halves_on_one_timeline_by_real_start_time(con, clock):
    a, b = _split_class(con, clock)
    out = classes.merge(con, b, a)                   # either order: the class that started first is the one kept
    assert out["kept"] == a and out["absorbed"] == b and out["offset"] == 1180.0
    got = classes.get(con, a)
    assert [(s["t_start"], s["source"]) for s in got["segments"]] == [
        (0.0, "mac"), (900.0, "mac"), (1182.0, "room"), (1210.0, "mac")]
    assert [s["t"] for s in got["slides"]] == [1192.0]
    assert [x["id"] for x in got["actions"]] == ["ca_1"]
    sess = got["session"]
    assert sess["ended_at"] == con.execute("SELECT ended_at FROM class_sessions WHERE id=?", (b,)).fetchone()[0]
    assert sess["status"] == "ended"                 # the notes must be made again, over the whole lesson
    assert sess["gaps"] == [{"from": 1000.0, "to": 1180.0, "reason": "restart"}]


def test_merge_hides_the_other_class_and_deletes_nothing(con, clock):
    a, b = _split_class(con, clock)
    before = con.execute("SELECT count(*) FROM class_segments").fetchone()[0]
    classes.merge(con, a, b)
    assert [c["id"] for c in classes.list(con)] == [a]
    assert con.execute("SELECT count(*) FROM class_segments").fetchone()[0] == before
    gone = classes.get(con, b)["session"]            # still there, pointing at the merged class
    assert gone["merged_into"] == a
    assert con.execute("SELECT count(*) FROM class_segments_fts WHERE class_segments_fts MATCH 'economy'").fetchone()[0] == 1


def test_merge_joins_his_typed_notes_and_shifts_the_second_half_timers(con, clock):
    a, b = _split_class(con, clock)
    classes.save_notes(con, a, "[00:10] hazy = back titration")
    classes.save_notes(con, b, "[00:30] atom economy\nno timer line")
    classes.merge(con, a, b)
    assert classes.get(con, a)["session"]["his_notes"] == (
        "[00:10] hazy = back titration\n[20:10] atom economy\nno timer line")


def test_merge_shifts_the_second_halfs_own_gaps(con, clock):
    a, b = _split_class(con, clock)
    con.execute("UPDATE class_sessions SET gaps=? WHERE id=?", ('[{"from": 100.0, "to": 160.0, "reason": "lid"}]', b))
    classes.merge(con, a, b)
    assert classes.get(con, a)["session"]["gaps"] == [{"from": 1000.0, "to": 1180.0, "reason": "restart"},
                                                      {"from": 1280.0, "to": 1340.0, "reason": "lid"}]


def test_merge_refuses_live_same_or_already_merged_classes(con, clock):
    import pytest
    from misty_engine.errors import Fatal
    a, b = _split_class(con, clock)
    with pytest.raises(Fatal):
        classes.merge(con, a, a)
    live = classes.start(con, clock, "Chem", None, None)["id"]
    with pytest.raises(Fatal):
        classes.merge(con, a, live)
    classes.end(con, clock, live)
    classes.merge(con, a, b)
    with pytest.raises(Fatal):
        classes.merge(con, a, b)                     # b is already inside a
    with pytest.raises(Fatal):
        classes.merge(con, b, live)


def test_merge_is_all_or_nothing(con, clock):
    import pytest
    from misty_engine.errors import Fatal
    a, b = _split_class(con, clock)
    with pytest.raises(Fatal):
        classes.merge(con, a, "cs_nope")
    assert [s["t_start"] for s in classes.get(con, b)["segments"]] == [2.0, 30.0]
    assert {c["id"] for c in classes.list(con)} == {a, b}


def _third(con, clock, a, b):
    """A third part of the same class, 30 min after the second ended."""
    clock.advance(1800)
    c = classes.start(con, clock, "Chem", "Chem Class, 16 Nov", None, "online")["id"]
    classes.add_segment(con, c, 1.0, 4.0, "mac", "now the third part")
    clock.advance(300)
    classes.end(con, clock, c)
    return c


def test_merge_many_joins_three_parts_in_time_order_and_keeps_earliest_title(con, clock):
    a, b = _split_class(con, clock)
    con.execute("UPDATE class_sessions SET title='Chem double' WHERE id=?", (a,))
    c = _third(con, clock, a, b)
    out = classes.merge_many(con, [c, a, b])                      # order given must not matter
    assert out["kept"] == a and sorted(out["absorbed_all"]) == sorted([b, c])
    got = classes.get(con, a)
    starts = [s["t_start"] for s in got["segments"]]
    assert starts == sorted(starts) and len(starts) == 5
    assert got["session"]["title"] == "Chem double"
    assert got["session"]["status"] == "ended"
    assert [x["id"] for x in classes.list(con)] == [a]
    assert len(got["actions"]) == 1 and len(got["slides"]) == 1


def test_merge_many_can_rename_and_is_all_or_nothing(con, clock):
    import pytest
    from misty_engine.errors import Fatal
    a, b = _split_class(con, clock)
    c = _third(con, clock, a, b)
    live = classes.start(con, clock, "Chem", None, None)["id"]
    with pytest.raises(Fatal):
        classes.merge_many(con, [a, b, live])                     # last fold fails after two would have worked
    assert {x["id"] for x in classes.list(con)} == {a, b, c, live}
    assert [s["t_start"] for s in classes.get(con, b)["segments"]] == [2.0, 30.0]
    with pytest.raises(Fatal):
        classes.merge_many(con, [a])
    classes.end(con, clock, live)
    out = classes.merge_many(con, [a, b], title="Titration, both parts")
    assert classes.get(con, out["kept"])["session"]["title"] == "Titration, both parts"


def _everything(con, clock, tmp_path, sid):
    """Give a class a row in every table that points at it, plus audio on disk."""
    con.execute("INSERT INTO teach_sources(id, kind, title, locator, class_id, added_at) VALUES "
                "('ts_1','class','Chem',?,?,0)", (sid, sid))
    con.execute("INSERT INTO teach_chunks(source_id, chunk_no, text_scrubbed) VALUES ('ts_1',0,'x')")
    con.execute("PRAGMA foreign_keys=OFF")                       # the topic it points at is not what is under test
    con.execute("INSERT INTO teach_evidence(topic_id, at, kind, weight, origin, origin_id, note) VALUES "
                "('t1',0,'said',1,'class',?,'n')", (sid + ":remember:abcd1234",))
    con.execute("PRAGMA foreign_keys=ON")
    d = tmp_path / "home" / "class-audio" / sid
    d.mkdir(parents=True)
    (d / "class.m4a").write_bytes(b"audio")
    return d


_TABLES = ("class_sessions", "class_segments", "class_slides", "class_actions", "teach_sources", "teach_chunks")


def _counts(con):
    n = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in _TABLES}
    n["evidence"] = con.execute("SELECT count(*) FROM teach_evidence WHERE origin='class'").fetchone()[0]
    n["fts"] = con.execute("SELECT count(*) FROM class_segments_fts WHERE class_segments_fts MATCH 'titration OR titrate'").fetchone()[0]
    return n


def test_delete_many_leaves_no_orphans_and_trashes_audio(con, clock, tmp_path):
    a, b = _split_class(con, clock)
    keep = classes.start(con, clock, "Phys", None, None)["id"]
    classes.add_segment(con, keep, 0, 1, "room", "momentum is conserved")
    classes.end(con, clock, keep)
    before = _counts(con)
    da, db_ = _everything(con, clock, tmp_path, a), _everything2(con, tmp_path, b)
    classes.merge_many(con, [a, b])                                # b is now an absorbed, hidden part of a
    gone = classes.delete_many(con, [a], home=tmp_path / "home", trash=tmp_path / "trash")
    assert sorted(gone) == sorted([a, b])                          # the hidden part goes too
    assert not da.exists() and not db_.exists()                    # audio left class-audio ...
    assert len(list((tmp_path / "trash").glob("Misty class *"))) == 2          # ... for the Trash, not the void
    assert (tmp_path / "trash" / f"Misty class {a}" / "class.m4a").read_bytes() == b"audio"
    left = _counts(con)
    assert left["class_sessions"] == 1 and left["class_segments"] == 1       # only Physics remains
    assert left["class_slides"] == 0 and left["class_actions"] == 0
    assert left["teach_sources"] == 0 and left["teach_chunks"] == 0 and left["evidence"] == 0 and left["fts"] == 0
    assert [c["id"] for c in classes.list(con)] == [keep]
    assert before["class_sessions"] == 3


def _everything2(con, tmp_path, sid):
    con.execute("INSERT INTO teach_sources(id, kind, title, locator, class_id, added_at) VALUES "
                "('ts_2','class','Chem',?,?,0)", (sid, sid))
    d = tmp_path / "home" / "class-audio" / sid
    d.mkdir(parents=True)
    (d / "class.m4a").write_bytes(b"audio")
    return d


def test_delete_many_several_at_once_and_unknown_id_changes_nothing(con, clock, tmp_path):
    import pytest
    from misty_engine.errors import Fatal
    a, b = _split_class(con, clock)
    with pytest.raises(Fatal):
        classes.delete_many(con, [a, "cs_nope"])
    assert {c["id"] for c in classes.list(con)} == {a, b}
    assert sorted(classes.delete_many(con, [a, b], home=tmp_path, trash=tmp_path / "t")) == sorted([a, b])
    assert classes.list(con) == [] and _counts(con)["class_segments"] == 0
