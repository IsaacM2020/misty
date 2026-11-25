"""Text extraction for files. Pure local code. Raises ExtractError with a short code, never content."""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from .errors import ExtractError

TEXT_EXT = {".md", ".markdown", ".txt"}
DOC_EXT = {".pdf", ".docx", ".pptx", ".xlsx"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp"}
MAX_BYTES = 50 * 1024 * 1024
PDF_MAX_PAGES = 30
XLSX_MAX_ROWS = 2000
MIN_LLM_WORDS = 300


class Extracted(NamedTuple):
    text: str
    word_count: int


def _pdf(path: Path) -> str:
    from pypdf import PdfReader
    r = PdfReader(str(path))
    if r.is_encrypted:
        try:
            ok = r.decrypt("")
        except Exception:
            ok = 0
        if not ok:
            raise ExtractError("PDF_ENCRYPTED")
    parts = []
    for page in r.pages[:PDF_MAX_PAGES]:
        parts.append(page.extract_text() or "")
    return "\n\n".join(parts)


def _docx(path: Path) -> str:
    import docx
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts.append("\t".join(c.text for c in row.cells))
    return "\n".join(parts)


def _pptx(path: Path) -> str:
    from pptx import Presentation
    parts = []
    for n, slide in enumerate(Presentation(str(path)).slides, start=1):
        parts.append(f"Slide {n}")
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    parts.append("\t".join(c.text for c in row.cells))
        if slide.has_notes_slide:
            parts.append(slide.notes_slide.notes_text_frame.text)
    return "\n".join(parts)


def _xlsx(path: Path) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        parts = []
        for ws in wb.worksheets:
            parts.append(f"Sheet {ws.title}")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= XLSX_MAX_ROWS:
                    break
                parts.append("\t".join("" if c is None else str(c) for c in row))
        return "\n".join(parts)
    finally:
        wb.close()


_DOC = {".pdf": _pdf, ".docx": _docx, ".pptx": _pptx, ".xlsx": _xlsx}


def extract_text(path: Path) -> Extracted:
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in TEXT_EXT and ext not in DOC_EXT:
        raise ExtractError("UNSUPPORTED")
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ExtractError("TOO_BIG")
        if ext in TEXT_EXT:
            text = path.read_bytes().decode("utf-8", errors="replace")
        else:
            text = _DOC[ext](path)
    except (ExtractError, FileNotFoundError, PermissionError):
        raise                   # callers map missing / unreadable files to their own skip codes
    except Exception:
        # any parser failure (corrupt zip, bad xref, ...) is a skipped file, never a crash
        raise ExtractError("EXTRACT_FAILED") from None
    text = text.replace("\x00", "")
    return Extracted(text, len(text.split()))
