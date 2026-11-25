"""E2: sources (file, page, syllabus, class). Pure code, no model call. A made-up stranger's subject, nothing about one person."""
import asyncio

import pytest

from misty_engine import chat
from misty_engine.errors import Fatal
from misty_engine.teach import sources as ts

LONG = ("Pacing a long run means starting slower than race pace. Negative splits are a sign of good control. " * 4 + "\n\n"
        "Fuel for runs over ninety minutes should be taken every forty minutes. ") * 3


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fileof(tmp_path):
    def make(name="plan.txt", text=LONG):
        p = tmp_path / name
        p.write_text(text)
        return str(p)
    return make


def add_class(ctx, cid="cs_aaaa1111", subject="Python", status="noted", merged=None, pruned=0, segs=("loops repeat things", "a list holds items")):
    ctx.con.execute("INSERT INTO class_sessions(id, subject, title, started_at, status, merged_into, pruned) VALUES (?,?,?,?,?,?,?)",
                    (cid, subject, f"{subject} class", ctx.clock.now() - 3600, status, merged, pruned))
    ids = []
    for i, t in enumerate(segs):
        cur = ctx.con.execute("INSERT INTO class_segments(session_id, t_start, t_end, source, text) VALUES (?,?,?,?,?)",
                              (cid, i * 10, i * 10 + 9, "room", t))
        ids.append(cur.lastrowid)
    return cid, ids


# ---- migration ----------------------------------------------------------------------------------------------------
def test_tables_exist(con):
    for t in ("teach_sources", "teach_chunks", "teach_chunks_fts"):
        assert con.execute("select count(*) from sqlite_master where name=?", (t,)).fetchone()[0] == 1
    assert 27 in [r[0] for r in con.execute("select version from schema_version")]


# ---- each kind comes in -------------------------------------------------------------------------------------------
def test_file_outside_the_index_keeps_scrubbed_text_and_never_touches_items(ctx, fileof):
    before = ctx.con.execute("select count(*) from items").fetchone()[0]
    r = run(ts.add(ctx, fileof()))
    assert r["kind"] == "file" and r["action"] == "added" and r["chars"] > 100
    assert ctx.con.execute("select count(*) from items").fetchone()[0] == before
    assert ctx.con.execute("select count(*) from vec_refs").fetchone()[0] == 0
    ch = ts.chunks(ctx, r["id"])
    assert ch and ch[0][0] == 0 and "Pacing" in ch[0][1]


def test_file_already_indexed_points_at_its_item_and_copies_nothing(ctx, fileof, add_item):
    path = fileof("notes.txt")
    add_item("it1", locator=path, title="Run notes")
    ctx.con.execute("INSERT INTO item_chunks(item_id, chunk_no, text_scrubbed) VALUES ('it1', 0, 'Interval sessions build speed.')")
    ctx.con.execute("INSERT INTO item_chunks(item_id, chunk_no, text_scrubbed) VALUES ('it1', 1, 'Easy runs build the base.')")
    r = run(ts.add(ctx, path))
    row = ctx.con.execute("select * from teach_sources where id=?", (r["id"],)).fetchone()
    assert row["item_id"] == "it1" and r["title"] == "Run notes"
    assert ctx.con.execute("select count(*) from teach_chunks").fetchone()[0] == 0
    assert ts.chunks(ctx, r["id"]) == [(0, "Interval sessions build speed."), (1, "Easy runs build the base.")]


def test_page_comes_in_through_the_full_page_reader(ctx, monkeypatch):
    async def fake(url):
        return LONG
    monkeypatch.setattr(ts.web, "read_full", fake)
    r = run(ts.add(ctx, "https://example.org/guide?x=1#top"))
    assert r["kind"] == "page" and ts.chunks(ctx, r["id"])


def test_syllabus_is_a_file_or_page_marked_as_one(ctx, fileof):
    r = run(ts.add(ctx, fileof("syl.txt"), kind="syllabus"))
    assert r["kind"] == "syllabus"
    with pytest.raises(Fatal):
        run(ts.add(ctx, "cs_aaaa1111", kind="syllabus"))


def test_class_uses_segment_ids_as_chunk_numbers_in_time_order(ctx):
    cid, ids = add_class(ctx)
    r = run(ts.add(ctx, cid))
    assert r["kind"] == "class"
    assert ts.chunks(ctx, r["id"]) == [(ids[0], "loops repeat things"), (ids[1], "a list holds items")]
    assert ctx.con.execute("select count(*) from teach_chunks").fetchone()[0] == 0


def test_class_by_subject_word_takes_the_latest_usable_one(ctx):
    add_class(ctx, "cs_old00001", "Python")
    ctx.con.execute("update class_sessions set started_at=started_at-86400 where id='cs_old00001'")
    add_class(ctx, "cs_new00002", "Python")
    r = run(ts.add(ctx, "class python"))
    assert ctx.con.execute("select class_id from teach_sources where id=?", (r["id"],)).fetchone()[0] == "cs_new00002"


@pytest.mark.parametrize("kw,why", [({"status": "live"}, "still going"), ({"merged": "cs_x"}, "joined"), ({"pruned": 1}, "transcript is gone")])
def test_class_refusals_name_the_reason(ctx, kw, why):
    cid, _ = add_class(ctx, **kw)
    with pytest.raises(Fatal) as e:
        run(ts.add(ctx, cid))
    assert why in str(e.value)


@pytest.mark.parametrize("status", ["ended", "noting", "noted"])
def test_class_statuses_that_qualify(ctx, status):
    cid, _ = add_class(ctx, status=status)
    assert run(ts.add(ctx, cid))["kind"] == "class"


# ---- the same thing twice is one source ---------------------------------------------------------------------------
def test_same_file_twice_is_one_source(ctx, fileof):
    p = fileof()
    a, b = run(ts.add(ctx, p)), run(ts.add(ctx, p))
    assert a["id"] == b["id"] and b["action"] == "existing"
    assert ctx.con.execute("select count(*) from teach_sources").fetchone()[0] == 1
    assert ctx.con.execute("select count(*) from teach_chunks where source_id=?", (a["id"],)).fetchone()[0] == len(ts.chunks(ctx, a["id"]))


def test_same_page_with_another_fragment_is_one_source(ctx, monkeypatch):
    async def fake(url):
        return LONG
    monkeypatch.setattr(ts.web, "read_full", fake)
    a = run(ts.add(ctx, "https://example.org/g"))
    b = run(ts.add(ctx, "https://example.org/g#part2"))
    assert a["id"] == b["id"]


def test_same_class_twice_is_one_source(ctx):
    cid, _ = add_class(ctx)
    assert run(ts.add(ctx, cid))["id"] == run(ts.add(ctx, cid))["id"]


# ---- find_quote ---------------------------------------------------------------------------------------------------
def test_find_quote_passes_a_real_quote_ignoring_case_spaces_and_curly_quotes(ctx):
    cid, ids = add_class(ctx, segs=("Don’t start the long run too fast, said the coach.",))
    sid = run(ts.add(ctx, cid))["id"]
    assert ts.find_quote(ctx, sid, ids[0], "don't  START the long run too FAST")


def test_find_quote_fails_a_changed_quote_and_a_wrong_chunk(ctx):
    cid, ids = add_class(ctx, segs=("loops repeat things", "a list holds items"))
    sid = run(ts.add(ctx, cid))["id"]
    assert not ts.find_quote(ctx, sid, ids[0], "loops repeat other things")
    assert not ts.find_quote(ctx, sid, ids[1], "loops repeat things")
    assert not ts.find_quote(ctx, sid, 99999, "loops repeat things")
    assert not ts.find_quote(ctx, "nope", 0, "loops")
    assert not ts.find_quote(ctx, sid, ids[0], "   ")


def test_find_quote_on_a_file_chunk(ctx, fileof):
    r = run(ts.add(ctx, fileof()))
    no, text = ts.chunks(ctx, r["id"])[0]
    assert ts.find_quote(ctx, r["id"], no, "negative splits are a sign of good control")
    assert not ts.find_quote(ctx, r["id"], no, "negative splits are a sign of bad control")


# ---- refusals -----------------------------------------------------------------------------------------------------
def test_secret_file_is_refused(ctx, tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=b")
    with pytest.raises(Fatal) as e:
        run(ts.add(ctx, str(p)))
    assert "secret" in str(e.value)


def test_private_frame_is_refused(ctx, fileof):
    with pytest.raises(Fatal) as e:
        run(ts.add(ctx, "this", doc=fileof(), private=True))
    assert "private" in str(e.value)


def test_this_without_an_open_document_is_refused(ctx):
    with pytest.raises(Fatal) as e:
        run(ts.add(ctx, "this", doc=""))
    assert "open" in str(e.value)


def test_this_uses_the_open_document(ctx, fileof):
    assert run(ts.add(ctx, "this", doc=fileof()))["kind"] == "file"


def test_too_big_is_refused_with_the_limit(ctx, fileof):
    with pytest.raises(Fatal) as e:
        run(ts.add(ctx, fileof("big.txt", "word " * 90000)))
    assert "400,000" in str(e.value)
    assert ctx.con.execute("select count(*) from teach_sources").fetchone()[0] == 0


def test_a_scan_is_refused(ctx, tmp_path, monkeypatch):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(ts.extract, "extract_text", lambda path: ts.extract.Extracted("  \n ", 0))
    with pytest.raises(Fatal) as e:
        run(ts.add(ctx, str(p)))
    assert "scan" in str(e.value)


def test_missing_and_unsupported_files(ctx, tmp_path):
    with pytest.raises(Fatal):
        run(ts.add(ctx, str(tmp_path / "nope.txt")))
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00\x01")
    with pytest.raises(Fatal) as e:
        run(ts.add(ctx, str(p)))
    assert "read" in str(e.value)


def test_private_address_page_is_refused(ctx):
    with pytest.raises(Fatal) as e:
        run(ts.add(ctx, "http://127.0.0.1/x"))
    assert "public" in str(e.value)


def test_secrets_in_a_file_are_scrubbed_before_storing(ctx, fileof):
    r = run(ts.add(ctx, fileof("k.txt", "Paces matter. key sk-or-v1-" + "a1b2c3d4" * 6 + " end. " + LONG)))
    assert all("a1b2c3d4a1b2c3d4" not in t for _, t in ts.chunks(ctx, r["id"]))


def test_unknown_what_is_refused(ctx):
    with pytest.raises(Fatal):
        run(ts.add(ctx, "the best thing ever"))


# ---- search -------------------------------------------------------------------------------------------------------
def test_search_finds_a_sentence_in_each_kind(ctx, fileof, add_item, monkeypatch):
    f = run(ts.add(ctx, fileof()))
    assert any("Negative splits" in t for _, t in ts.search(ctx, f["id"], "negative splits", 3))
    cid, ids = add_class(ctx, segs=("the mitochondria is the powerhouse", "ribosomes make protein"))
    c = run(ts.add(ctx, cid))
    assert ts.search(ctx, c["id"], "powerhouse", 3)[0][0] == ids[0]
    path = fileof("n.txt")
    add_item("it9", locator=path)
    ctx.con.execute("INSERT INTO item_chunks(item_id, chunk_no, text_scrubbed) VALUES ('it9', 0, 'Tempo runs sit near threshold.')")
    ctx.con.execute("INSERT INTO fts(text, ref_kind, ref_id) VALUES ('Tempo runs sit near threshold.', 'chunk', 'it9:0')")
    ctx.con.execute("INSERT INTO fts(text, ref_kind, ref_id) VALUES ('Tempo runs elsewhere.', 'chunk', 'other:0')")
    i = run(ts.add(ctx, path))
    hits = ts.search(ctx, i["id"], "tempo threshold", 3)
    assert hits and hits[0][0] == 0 and len(hits) == 1
    async def fake(url):
        return LONG
    monkeypatch.setattr(ts.web, "read_full", fake)
    p = run(ts.add(ctx, "https://example.org/pg"))
    assert ts.search(ctx, p["id"], "fuel forty minutes", 3)


def test_search_stays_inside_its_source(ctx):
    a, _ = add_class(ctx, "cs_aaaa0001", segs=("alpha topic here",))
    b, _ = add_class(ctx, "cs_bbbb0002", segs=("alpha topic there",))
    sa = run(ts.add(ctx, a))["id"]
    assert len(ts.search(ctx, sa, "alpha", 5)) == 1


def test_search_with_no_words_is_empty(ctx):
    cid, _ = add_class(ctx)
    assert ts.search(ctx, run(ts.add(ctx, cid))["id"], "?!", 3) == []


# ---- show ---------------------------------------------------------------------------------------------------------
def test_show_lists_sources_with_length(ctx, fileof):
    run(ts.add(ctx, fileof()))
    cid, _ = add_class(ctx)
    run(ts.add(ctx, cid))
    rows = ts.show(ctx)
    assert {r["kind"] for r in rows} == {"file", "class"}
    assert all("length" in r and "title" in r for r in rows)


# ---- the chat tool ------------------------------------------------------------------------------------------------
def call(ctx, args):
    async def status(_): ...
    async def confirm(*a, **k): return True
    return run(chat.run_tool(ctx, "course", args, on_status=status, confirm=confirm))


def test_tool_add_source_then_show(ctx, fileof):
    r = call(ctx, {"action": "add_source", "what": fileof()})
    assert r["ok"] and r["next"] and r["title"]
    s = call(ctx, {"action": "show"})
    assert s["ok"] and len(s["sources"]) == 1


def test_tool_refusal_is_plain_not_a_crash(ctx):
    r = call(ctx, {"action": "add_source", "what": "this"})
    assert r["ok"] is False and "open" in r["error"]


def test_tool_unknown_action(ctx):
    assert call(ctx, {"action": "make"})["ok"] is False


def test_tool_refuses_text_she_read(ctx, fileof):
    async def status(_): ...
    async def confirm(*a, **k): return True
    r = run(chat.run_tool(ctx, "course", {"action": "add_source", "what": fileof()}, on_status=status, confirm=confirm, source="text"))
    assert r["ok"] is False


def test_course_tool_is_in_the_list_with_an_action_enum():
    t = next(t for t in chat.TOOLS if t["function"]["name"] == "course")
    assert t["function"]["parameters"]["properties"]["action"]["enum"][:2] == ["add_source", "show"]
    assert "course" in chat.COURSE_RULE.lower() or "source" in chat.COURSE_RULE.lower()


def test_course_rule_is_in_the_normal_prompt_and_not_the_exam_prompt(ctx):
    normal = "".join(chat.system_parts(ctx, "hi"))
    exam = "".join(chat.system_parts(ctx, "hi", exam=True))
    assert chat.COURSE_RULE.strip() in normal and chat.COURSE_RULE.strip() not in exam
