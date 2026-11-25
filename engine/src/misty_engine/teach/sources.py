"""E2: what he can be taught from. A source is a class, a file, a page or a syllabus.

No copies where the text already exists: a file that is already indexed points at its item (item_chunks), a class points
at its class session (class_segments, whose row ids are the chunk numbers the class notes already cite). Only pages and
files outside his indexed folders keep scrubbed text, in teach_chunks. Nothing here writes to `items` or `vec_refs`, so
the memory pipeline never compiles a teaching source into the wiki. No model call. Nothing knows a subject or a person.
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from .. import chunk, extract, web
from ..errors import Fatal
from ..index import fts_query
from ..logs import log_event
from ..scrub import is_secret_file, scrub_text

KINDS = ("class", "file", "page", "syllabus")
MAX_CHARS = 400_000                       # a source this big would cost too much to map: ask him for a part instead
QUALIFYING = ("ended", "noting", "noted")  # a class still being heard, or one that never got notes, is not a source yet
MIN_WORDS = 20                            # fewer words than this from a PDF means it is a picture of text


def _fail(why: str) -> Fatal:
    """A Fatal whose text is the plain reason (the chat tool says it back to him as it is)."""
    return Fatal(why)


def _norm(s: str) -> str:
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def _page_key(url: str) -> str:
    u = urlparse(url.strip())
    return urlunparse((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), "", u.query, ""))


# ---- which thing did he mean -----------------------------------------------------------------------------------
def _class_for(ctx, what: str):
    w = what.strip()
    if re.fullmatch(r"cs_[0-9a-f]+", w):
        return ctx.con.execute("SELECT * FROM class_sessions WHERE id=?", (w,)).fetchone() or _fail("I can't find that class")
    m = re.match(r"(?i)^(?:my\s+)?class(?:es)?\b\s*(?:in|for|on|of)?\s*(.*)$", w)
    if not m:
        return None
    subj = m.group(1).strip().lower()
    rows = ctx.con.execute("SELECT * FROM class_sessions WHERE merged_into IS NULL AND pruned=0 AND status IN ('ended','noting','noted') "
                           "ORDER BY started_at DESC").fetchall()
    for r in rows:
        if not subj or subj in r["subject"].lower():
            return r
    raise _fail("I can't find a finished class" + (f" for {subj}" if subj else ""))


def _check_class(row) -> None:
    if row["merged_into"]:
        raise _fail("that class was joined into another one, so I would use the one it joined")
    if row["pruned"]:
        raise _fail("that class's transcript is gone, so I have nothing to teach from")
    if row["status"] not in QUALIFYING:
        raise _fail("that class is still going or never got written up, so I can't use it yet")


def _existing(ctx, locator: str):
    return ctx.con.execute("SELECT * FROM teach_sources WHERE locator=? AND status='active' ORDER BY added_at LIMIT 1", (locator,)).fetchone()


def _reply(row, action: str) -> dict:
    return {"id": row["id"], "kind": row["kind"], "title": row["title"], "chars": row["chars"], "action": action}


def _insert(ctx, kind, title, locator, chars, item_id=None, class_id=None) -> str:
    sid = "src_" + secrets.token_hex(5)
    ctx.con.execute("INSERT INTO teach_sources(id, kind, title, locator, item_id, class_id, chars, added_at) VALUES (?,?,?,?,?,?,?,?)",
                    (sid, kind, title, locator, item_id, class_id, chars, ctx.clock.now()))
    return sid


def _store_chunks(ctx, sid: str, text: str) -> None:
    for i, c in enumerate(chunk.chunk_text(text)):
        ctx.con.execute("INSERT INTO teach_chunks(source_id, chunk_no, text_scrubbed) VALUES (?,?,?)", (sid, i, c))


# ---- add ---------------------------------------------------------------------------------------------------------
async def add(ctx, what: str, kind: str | None = None, *, doc: str = "", private: bool = False) -> dict:
    """Make `what` a source: a link, a file path, a class (its id, or "class <subject>"), or "this" (the open document).
    Returns {id, kind, title, chars, action: added | existing}. Refusals raise Fatal with the plain reason."""
    what = (what or "").strip()
    if kind not in (None, "syllabus"):
        raise _fail("a source is a class, a file, a page or a syllabus")
    if not what:
        raise _fail("tell me what to learn from: a file, a link or a class")
    if what.lower() in ("this", "this one", "it", "this document", "this page", "this file"):
        if private:
            raise _fail("what you have open is private, so I don't read it")
        if not doc:
            raise _fail("I can't see anything open to learn from: give me the file or the link")
        what = doc
    cls = None if what.startswith(("http://", "https://", "/", "~")) else _class_for(ctx, what)
    if cls is not None:
        if kind == "syllabus":
            raise _fail("a syllabus is a file or a page, not a class")
        _check_class(cls)
        old = _existing(ctx, cls["id"])
        if old is not None:
            return _reply(old, "existing")
        n = ctx.con.execute("SELECT COALESCE(SUM(LENGTH(text)),0) FROM class_segments WHERE session_id=?", (cls["id"],)).fetchone()[0]
        if n == 0:
            raise _fail("that class has no transcript")
        if n > MAX_CHARS:
            raise _fail(f"that class is longer than {MAX_CHARS:,} characters, which is too much at once")
        sid = _insert(ctx, "class", cls["title"] or f"{cls['subject']} class", cls["id"], n, class_id=cls["id"])
        log_event("TEACH_SOURCE", kind="class", n=n)
        return _reply(ctx.con.execute("SELECT * FROM teach_sources WHERE id=?", (sid,)).fetchone(), "added")
    if what.startswith(("http://", "https://")):
        return await _add_page(ctx, what, kind or "page")
    return _add_file(ctx, what, kind or "file")


async def _add_page(ctx, url: str, kind: str) -> dict:
    key = _page_key(url)
    old = _existing(ctx, key)
    if old is not None:
        return _reply(old, "existing")
    if not ctx.net.online:
        raise _fail("Misty is offline, so I can't open that link")
    try:
        text = await web.read_full(url)
    except ValueError as e:
        raise _fail("I can only read public web pages" if "public" in str(e) else f"I couldn't open that page ({e})") from None
    return _keep(ctx, kind, key, urlparse(url).netloc + urlparse(url).path.rstrip("/"), text)


def _add_file(ctx, what: str, kind: str) -> dict:
    p = Path(os.path.expanduser(what))
    real = os.path.realpath(p)
    if is_secret_file(p):
        raise _fail("that looks like a secret file (keys or passwords), so I don't read it")
    old = _existing(ctx, real)
    if old is not None:
        return _reply(old, "existing")
    item = ctx.con.execute("SELECT id, title FROM items WHERE locator IN (?,?) LIMIT 1", (what, real)).fetchone()
    if item is not None:
        n = ctx.con.execute("SELECT COALESCE(SUM(LENGTH(text_scrubbed)),0), COUNT(*) FROM item_chunks WHERE item_id=?", (item["id"],)).fetchone()
        if n[1]:
            if n[0] > MAX_CHARS:
                raise _fail(f"that is longer than {MAX_CHARS:,} characters, which is too much at once: pick a part")
            sid = _insert(ctx, kind, item["title"] or p.name, real, n[0], item_id=item["id"])
            log_event("TEACH_SOURCE", kind=kind, n=n[0], cached=True)
            return _reply(ctx.con.execute("SELECT * FROM teach_sources WHERE id=?", (sid,)).fetchone(), "added")
    try:
        got = extract.extract_text(p)
    except FileNotFoundError:
        raise _fail("I can't find that file") from None
    except PermissionError:
        raise _fail("I'm not allowed to open that file") from None
    except extract.ExtractError as e:
        code = str(e)
        raise _fail("that file is too big to read" if code == "TOO_BIG" else "that PDF is locked" if code == "PDF_ENCRYPTED"
                    else "I can't read that kind of file yet") from None
    if p.suffix.lower() == ".pdf" and len(got.text.split()) < MIN_WORDS:
        raise _fail("this is a scan, I can't read it yet")
    return _keep(ctx, kind, real, p.name, got.text)


def _keep(ctx, kind: str, locator: str, title: str, text: str) -> dict:
    text = scrub_text(text.replace("\x00", ""), "file").text.strip()
    if not text:
        raise _fail("there is no text in that to learn from")
    if len(text) > MAX_CHARS:
        raise _fail(f"that is longer than {MAX_CHARS:,} characters, which is too much at once: pick a part")
    sid = _insert(ctx, kind, title, locator, len(text))
    _store_chunks(ctx, sid, text)
    log_event("TEACH_SOURCE", kind=kind, n=len(text))
    return _reply(ctx.con.execute("SELECT * FROM teach_sources WHERE id=?", (sid,)).fetchone(), "added")


# ---- read --------------------------------------------------------------------------------------------------------
def _row(ctx, source_id: str):
    return ctx.con.execute("SELECT * FROM teach_sources WHERE id=?", (source_id,)).fetchone()


def chunks(ctx, source_id: str) -> list[tuple[int, str]]:
    """[(chunk_no, text)] in reading order. For a class the number is the class_segments id."""
    s = _row(ctx, source_id)
    if s is None:
        return []
    if s["class_id"]:
        rows = ctx.con.execute("SELECT id, text FROM class_segments WHERE session_id=? ORDER BY t_start, id", (s["class_id"],)).fetchall()
        return [(r["id"], scrub_text(r["text"], "file").text) for r in rows]
    if s["item_id"]:
        rows = ctx.con.execute("SELECT chunk_no, text_scrubbed FROM item_chunks WHERE item_id=? ORDER BY chunk_no", (s["item_id"],)).fetchall()
    else:
        rows = ctx.con.execute("SELECT chunk_no, text_scrubbed FROM teach_chunks WHERE source_id=? ORDER BY chunk_no", (source_id,)).fetchall()
    return [(r[0], r[1]) for r in rows]


def _chunk(ctx, s, chunk_no: int) -> str | None:
    if s["class_id"]:
        r = ctx.con.execute("SELECT text FROM class_segments WHERE id=? AND session_id=?", (chunk_no, s["class_id"])).fetchone()
    elif s["item_id"]:
        r = ctx.con.execute("SELECT text_scrubbed FROM item_chunks WHERE item_id=? AND chunk_no=?", (s["item_id"], chunk_no)).fetchone()
    else:
        r = ctx.con.execute("SELECT text_scrubbed FROM teach_chunks WHERE source_id=? AND chunk_no=?", (s["id"], chunk_no)).fetchone()
    return r[0] if r else None


def chunk_text(ctx, source_id: str, chunk_no: int) -> str | None:
    """The text of one cited chunk, or None when the source or the number does not exist."""
    s = _row(ctx, source_id)
    return None if s is None else _chunk(ctx, s, chunk_no)


def find_quote(ctx, source_id: str, chunk_no: int, quote: str) -> bool:
    """The quote-cite check: is `quote` really in that chunk? Case, spaces and curly quotes are ignored."""
    s = _row(ctx, source_id)
    q = _norm(quote)
    if s is None or not q:
        return False
    text = _chunk(ctx, s, chunk_no)
    return text is not None and q in _norm(text)


def search(ctx, source_id: str, query: str, k: int = 5) -> list[tuple[int, str]]:
    """The k best chunks of one source for a query: [(chunk_no, text)]. Word search first (the right table for the kind);
    for a file already in the index, meaning search only fills what words did not find."""
    s = _row(ctx, source_id)
    q = fts_query(query)
    if s is None or q is None:
        return []
    c = ctx.con
    if s["class_id"]:
        rows = c.execute("SELECT g.id, g.text FROM class_segments_fts f JOIN class_segments g ON g.id=f.rowid "
                         "WHERE class_segments_fts MATCH ? AND g.session_id=? ORDER BY rank LIMIT ?", (q, s["class_id"], k)).fetchall()
        return [(r[0], r[1]) for r in rows]
    if s["item_id"]:
        rows = c.execute("SELECT ref_id FROM fts WHERE fts MATCH ? AND ref_kind='chunk' AND ref_id LIKE ? ORDER BY rank LIMIT ?",
                         (q, s["item_id"] + ":%", k)).fetchall()
        got = [int(r[0].rsplit(":", 1)[1]) for r in rows]
        if len(got) < k and ctx.embedder is not None and ctx.index is not None:
            try:
                for h in ctx.index.vector_search(ctx.embedder.embed_query(query), k=200, ref_kinds=("chunk",)):
                    if h.item_id == s["item_id"] and h.ref_id.startswith(s["item_id"] + ":"):
                        n = int(h.ref_id.rsplit(":", 1)[1])
                        if n not in got:
                            got.append(n)
                    if len(got) >= k:
                        break
            except Exception:                      # the second pass is a bonus: words already answered
                pass
        return [(n, t) for n in got[:k] if (t := _chunk(ctx, s, n)) is not None]
    rows = c.execute("SELECT t.chunk_no, t.text_scrubbed FROM teach_chunks_fts f JOIN teach_chunks t ON t.rowid=f.rowid "
                     "WHERE teach_chunks_fts MATCH ? AND t.source_id=? ORDER BY rank LIMIT ?", (q, source_id, k)).fetchall()
    return [(r[0], r[1]) for r in rows]


def show(ctx) -> list[dict]:
    """Every active source with its length in plain words."""
    out = []
    for s in ctx.con.execute("SELECT * FROM teach_sources WHERE status='active' ORDER BY added_at, id"):
        pages = max(1, round(s["chars"] / 2000))
        out.append({"id": s["id"], "kind": s["kind"], "title": s["title"], "chars": s["chars"],
                    "length": f"about {pages} page{'s' if pages != 1 else ''}"})
    return out
