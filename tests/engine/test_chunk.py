from misty_engine.chunk import chunk_text


def test_chunks_bounded_and_cover_text():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(30))
    chunks = chunk_text(text)
    assert all(len(c) <= 1600 + 200 for c in chunks)
    for i in range(30):
        assert any(f"Paragraph {i}." in c for c in chunks)


def test_chunk_edge_cases():
    assert chunk_text("") == [] and chunk_text("short") == ["short"]
    assert all(len(c) <= 1800 for c in chunk_text("x" * 10_000))


def test_chunks_overlap():
    text = "\n\n".join(f"P{i} " + "word " * 100 for i in range(10))
    c = chunk_text(text, max_chars=600, overlap=100)
    assert len(c) > 2
    for a, b in zip(c, c[1:]):
        assert b.startswith(a[-100:])


def test_long_paragraph_splits_on_sentences():
    para = " ".join(f"Sentence number {i} is here." for i in range(200))
    c = chunk_text(para, max_chars=500, overlap=50)
    assert len(c) > 5 and all(len(x) <= 550 for x in c)
