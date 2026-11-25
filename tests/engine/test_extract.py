import pytest

from misty_engine.errors import ExtractError
from misty_engine.extract import MAX_BYTES, extract_text


def test_extract_md(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# Hi\n\nhello world")
    assert extract_text(p).word_count == 4


def test_extract_strips_nul_bytes(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"he\x00llo")
    assert extract_text(p).text == "hello"


def test_extract_docx_pptx_xlsx(make_docx, make_pptx, make_xlsx):
    assert "iGEM" in extract_text(make_docx("iGEM wiki draft")).text
    assert "slide text" in extract_text(make_pptx("slide text")).text
    assert "42" in extract_text(make_xlsx([["runs", 42]])).text


def test_extract_xlsx_row_cap(make_xlsx):
    rows = [[f"r{i}"] for i in range(2500)]
    t = extract_text(make_xlsx(rows)).text
    assert "r1999" in t and "r2000" not in t


def test_extract_pdf_first_30_pages_only(make_pdf):
    e = extract_text(make_pdf(pages=40))
    assert "page 30" in e.text and "page 31" not in e.text


def test_extract_bad_pdf_raises_extract_error(tmp_path, make_pdf):      # Review Focus 3
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(ExtractError) as e:
        extract_text(bad)
    assert e.value.code == "EXTRACT_FAILED"
    with pytest.raises(ExtractError) as e:
        extract_text(make_pdf(pages=2, encrypted=True))
    assert e.value.code == "PDF_ENCRYPTED"


def test_extract_corrupt_docx(tmp_path):
    p = tmp_path / "bad.docx"
    p.write_bytes(b"not a zip")
    with pytest.raises(ExtractError) as e:
        extract_text(p)
    assert e.value.code == "EXTRACT_FAILED"


def test_extract_unsupported(tmp_path):
    p = tmp_path / "a.exe"
    p.write_bytes(b"x")
    with pytest.raises(ExtractError) as e:
        extract_text(p)
    assert e.value.code == "UNSUPPORTED"


def test_extract_too_big(tmp_path):
    p = tmp_path / "big.txt"
    with open(p, "wb") as f:
        f.truncate(MAX_BYTES + 1)
    with pytest.raises(ExtractError) as e:
        extract_text(p)
    assert e.value.code == "TOO_BIG"
