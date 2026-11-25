import pytest

from misty_engine.index import fts_query

A, B, C = "it_0000000000000001", "it_000000000000000a", "it_000000000000000b"


def test_fts_query_shape():
    assert fts_query("what's up?") == '"what" OR "s" OR "up"'
    assert fts_query("🏏🔥") is None and fts_query("") is None
    assert fts_query(" ".join(f"w{i}" for i in range(40))).count("OR") == 15      # first 16 tokens


def test_replace_chunks_then_search(con, idx, emb, add_item):
    add_item(A)
    chunks = ["iGEM wiki deadline is Friday", "cricket nets on Saturday"]
    idx.replace_item_chunks(A, "folders", chunks, emb.embed_documents(chunks))
    assert idx.fts_search("igem deadline")[0].ref_id == f"{A}:0"
    assert idx.vector_search(emb.embed_query("iGEM wiki deadline"))[0].item_id == A


def test_replace_removes_old_rows(con, idx, emb, add_item):
    add_item(A)
    idx.replace_item_chunks(A, "folders", ["old text"], emb.embed_documents(["old text"]))
    idx.replace_item_chunks(A, "folders", ["new text"], emb.embed_documents(["new text"]))
    assert idx.fts_search("old") == []
    assert con.execute("select count(*) from vec_refs where item_id=?", (A,)).fetchone()[0] == 1
    assert con.execute("select count(*) from vectors").fetchone()[0] == 1
    assert con.execute("select count(*) from item_chunks").fetchone()[0] == 1


def test_near_duplicate_same_source_only(idx, emb, add_item):
    v = emb.embed_documents(["the same document body"])[0]
    for i, s in ((A, "folders"), (B, "folders"), (C, "gmail")):
        add_item(i, s)
    idx.set_item_vector(A, "folders", v)
    assert idx.near_duplicate(B, "folders", v) == A
    assert idx.near_duplicate(C, "gmail", v) is None
    assert idx.near_duplicate(A, "folders", v) is None           # never itself


def test_near_duplicate_ignores_chunk_vectors(idx, emb, add_item):
    v = emb.embed_documents(["same body"])[0]
    add_item(A)
    add_item(B)
    idx.replace_item_chunks(A, "folders", ["same body"], emb.embed_documents(["same body"]))
    assert idx.near_duplicate(B, "folders", v) is None           # only 'item' vectors count


def test_images_and_page_kinds(idx, emb, add_item):
    add_item(A, kind="image")
    v = emb.embed_documents(["red circle"])[0]
    idx.set_image(A, "folders", v)
    assert [h.ref_kind for h in idx.vector_search(v)] == ["image"]
    idx.upsert_page("projects/igem.md", "iGEM project page about wiki", v)
    assert idx.fts_search("igem", ref_kinds=("page",))[0].ref_id == "projects/igem.md"
    assert idx.fts_search("igem") == []                          # default is chunks only
    idx.upsert_page("projects/igem.md", "completely different words", v)
    assert idx.fts_search("igem", ref_kinds=("page",)) == []
    idx.remove_page("projects/igem.md")
    assert idx.fts_search("different", ref_kinds=("page",)) == []
    assert [h.ref_kind for h in idx.vector_search(v, ref_kinds=("page",))] == []


def test_page_and_chunk_rowids_never_collide(idx, emb, add_item):
    v = emb.embed_documents(["x"])[0]
    idx.upsert_page("a.md", "page words", v)
    add_item(A)
    idx.replace_item_chunks(A, "folders", ["chunk words"] * 5, emb.embed_documents(["chunk words"] * 5))
    assert len(idx.fts_search("chunk")) == 5 and len(idx.fts_search("page", ref_kinds=("page",))) == 1


def test_remove_item_removes_everything(con, idx, emb, add_item):
    add_item(A)
    v = emb.embed_documents(["body"])
    idx.replace_item_chunks(A, "folders", ["body"], v)
    idx.set_item_vector(A, "folders", v[0])
    idx.set_image(A, "folders", v[0])
    idx.remove_item(A)
    for t in ("vectors", "vec_refs", "item_chunks", "fts"):
        assert con.execute(f"select count(*) from {t}").fetchone()[0] == 0, t


@pytest.mark.parametrize("q", ["what's up?", '"unbalanced', "AND OR NOT", "🏏🔥", "kya scene hai bhai", "", "*", "NEAR(a b)",
                               "a:b", "(", "-x", "^y"])
def test_fts_query_never_raises(idx, q):                                 # Review Focus 4
    assert isinstance(idx.fts_search(q), list)


def test_vector_search_empty_index(idx, emb):
    assert idx.vector_search(emb.embed_query("anything")) == []
