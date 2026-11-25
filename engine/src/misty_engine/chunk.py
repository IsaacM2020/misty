"""Paragraph-packing chunker. Pure code."""
from __future__ import annotations

import re

_PARA = re.compile(r"\n\s*\n")
_SENT = re.compile(r"(?<=[.!?])\s+")


def _units(text: str, max_chars: int) -> list[tuple[str, str]]:
    """Split text into (piece, separator-before) units that each fit in max_chars."""
    out: list[tuple[str, str]] = []
    for para in _PARA.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            out.append((para, "\n\n"))
            continue
        sep = "\n\n"
        for sent in _SENT.split(para):
            for i in range(0, len(sent), max_chars):
                out.append((sent[i:i + max_chars], sep))
                sep = " "
    return out


def chunk_text(text: str, max_chars: int = 1600, overlap: int = 200) -> list[str]:
    bodies: list[str] = []
    cur = ""
    for piece, sep in _units(text, max_chars):
        if not cur:
            cur = piece
        elif len(cur) + len(sep) + len(piece) <= max_chars:
            cur += sep + piece
        else:
            bodies.append(cur)
            cur = piece
    if cur:
        bodies.append(cur)
    chunks: list[str] = []
    for body in bodies:
        chunks.append(body if not chunks else chunks[-1][-overlap:] + body)
    return chunks


def join_chunks(chunks: list[str], overlap: int = 200) -> str:
    """Inverse of chunk_text: chunk i starts with the last `overlap` chars of chunk i-1, so drop them again."""
    out: list[str] = []
    for i, c in enumerate(chunks):
        out.append(c if i == 0 else c[min(overlap, len(chunks[i - 1])):])
    return "\n\n".join(out)
