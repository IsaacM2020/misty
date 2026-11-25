import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

import keyring
import pytest

from fakes import InMemoryKeyring
from misty_engine.clock import FakeClock
from misty_engine.config import Settings, load_pinned
from misty_engine.db import connect, migrate
from misty_engine.net import NetState
from misty_engine.openrouter import OpenRouterClient
from misty_engine.paths import MistyPaths
from misty_engine.spend import SpendMeter

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def misty_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MISTY_HOME", str(tmp_path / "Misty"))
    p = MistyPaths.from_env()
    p.ensure()
    return p


@pytest.fixture(autouse=True)
def no_gmail_pacing(monkeypatch):
    """The real pace waits seconds per batch to respect Google's quota; tests never wait."""
    from misty_engine.sources import gmail
    monkeypatch.setattr(gmail, "PACE_S", 0.0)
    monkeypatch.setattr(gmail, "LOAD_GAP_S", 0.0)


@pytest.fixture(autouse=True)
def fake_keyring():
    kr = InMemoryKeyring()
    keyring.set_keyring(kr)
    return kr


@pytest.fixture
def short_tmp():
    d = Path(tempfile.mkdtemp(dir="/tmp", prefix="mt"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    yield
    root.handlers = saved
    root.setLevel(level)


@pytest.fixture
def tz_utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def clock():
    return FakeClock(1_792_000_000.0)


@pytest.fixture
def pinned():
    return load_pinned()


@pytest.fixture
def con(misty_home):
    c = connect(misty_home.db)
    migrate(c, 768)
    yield c
    c.close()


@pytest.fixture
def settings(con):
    st = Settings(con)
    # no test may ever walk the real home directory or open a real source by default
    st.set("folders", [])
    st.set("live_compile_min_items", 1)      # tests compile at once; the wait has its own test
    st.set("sources_enabled", {k: False for k in ("folders", "repos", "claude_mem", "graphify", "gmail", "gcal",
                                                  "imessage", "whatsapp")})
    return st


@pytest.fixture
def chat_ok():
    return json.loads((FIX / "chat_response_example.json").read_text())


@pytest.fixture
def client(con, settings, clock, pinned, httpserver):
    return OpenRouterClient(
        pinned=pinned, settings=settings, spend=SpendMeter(con, clock, settings), net=NetState(),
        key_provider=lambda: "sk-or-v1-test", clock=clock, base_url=httpserver.url_for("/api/v1"))


# ---- document fixtures, generated at test time (no binary files in the repo)

@pytest.fixture
def make_docx(tmp_path):
    def make(text: str, name: str = "doc.docx") -> Path:
        import docx
        d = docx.Document()
        d.add_paragraph(text)
        p = tmp_path / name
        d.save(p)
        return p
    return make


@pytest.fixture
def make_pptx(tmp_path):
    def make(text: str, name: str = "deck.pptx") -> Path:
        from pptx import Presentation
        pr = Presentation()
        s = pr.slides.add_slide(pr.slide_layouts[5])
        s.shapes.title.text = text
        p = tmp_path / name
        pr.save(p)
        return p
    return make


@pytest.fixture
def make_xlsx(tmp_path):
    def make(rows: list[list], name: str = "sheet.xlsx") -> Path:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        for r in rows:
            ws.append(r)
        p = tmp_path / name
        wb.save(p)
        return p
    return make


def _pdf_bytes(pages: int, body: str = "") -> bytes:
    """A tiny valid PDF whose page N says 'page N' (Helvetica, one content stream per page)."""
    objs: list[bytes] = []
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i in range(pages):
        page_no, content_no = 4 + 2 * i, 5 + 2 * i
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents {content_no} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>".encode())
        stream = f"BT /F1 12 Tf 10 100 Td (page {i + 1} {body}) Tj ET".encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@pytest.fixture
def make_pdf(tmp_path):
    def make(pages: int = 1, encrypted: bool = False, name: str | None = None) -> Path:
        p = tmp_path / (name or ("enc.pdf" if encrypted else "doc.pdf"))
        raw = _pdf_bytes(pages)
        if encrypted:
            import io
            from pypdf import PdfReader, PdfWriter
            w = PdfWriter(clone_from=PdfReader(io.BytesIO(raw)))
            w.encrypt("pw")
            with open(p, "wb") as f:
                w.write(f)
        else:
            p.write_bytes(raw)
        return p
    return make


@pytest.fixture
def tree(tmp_path):
    """A fake home directory."""
    t = tmp_path / "home"
    (t / "Desktop").mkdir(parents=True)
    (t / "Desktop/essay.pdf").write_bytes(_pdf_bytes(1, "essay " * 400))
    (t / "Desktop/photo.heic").write_bytes(b"heic")
    (t / "Desktop/.env").write_text("SECRET=1\n")
    (t / "Desktop/script.py").write_text("print('hi')\n")
    (t / "Desktop/short.md").write_text("a short note about cricket nets\n")
    (t / "Desktop/cloud-only.pdf").write_bytes(b"%PDF-1.4 y")
    (t / "Desktop/.old.pdf.icloud").write_bytes(b"stub")
    (t / "Notes/.git").mkdir(parents=True)
    (t / "Notes/notes.md").write_text("isaac os notes\n")
    for d in ("Code/Water/oldapp", "Code/_archive", "Old Knowledge Base"):
        (t / d).mkdir(parents=True)
        (t / d / "x.md").write_text("must never be indexed\n")
    tool = t / "Code/Water/tool"
    (tool / ".git").mkdir(parents=True)
    (tool / "src").mkdir()
    (tool / "src/main.py").write_text("def secret_impl():\n    return 1\n")
    (tool / "README.md").write_text("# Tool README\n\nA tool that does things.\n")
    (tool / "node_modules").mkdir()
    (tool / "node_modules/junk.md").write_text("junk")
    return t


@pytest.fixture
def emb():
    from fakes import FakeEmbedder
    return FakeEmbedder()


@pytest.fixture
def add_item(con, clock):
    """Insert a bare items row so chunks and vectors can reference it."""
    def add(item_id: str, source: str = "folders", kind: str = "file", title: str = "t",
            status: str = "found", modified_at: str | None = None, locator: str | None = None) -> str:
        con.execute(
            "INSERT OR IGNORE INTO items(id, source, kind, locator, title, status, modified_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (item_id, source, kind, locator or f"/x/{item_id}", title, status, modified_at, clock.now()))
        return item_id
    return add


@pytest.fixture
def idx(con):
    from misty_engine.index import Index
    return Index(con, 768)


@pytest.fixture
def ctx(con, settings, clock, pinned, emb, idx, misty_home):
    from misty_engine.ctx import Ctx
    from misty_engine.jobs import JobQueue
    from misty_engine.net import NetState
    from misty_engine.openrouter import OpenRouterClient
    from misty_engine.spend import SpendMeter
    spend, net = SpendMeter(con, clock, settings), NetState()
    orc = OpenRouterClient(pinned=pinned, settings=settings, spend=spend, net=net,
                           key_provider=lambda: "sk-or-v1-test", clock=clock)
    return Ctx(paths=misty_home, pinned=pinned, con=con, settings=settings,
               queue=JobQueue(con, clock, settings), spend=spend, net=net, clock=clock,
               openrouter=orc, embedder=emb, index=idx)


@pytest.fixture
def fake_or(httpserver):
    from fake_openrouter import FakeOpenRouter
    return FakeOpenRouter(httpserver)


@pytest.fixture
def fctx(ctx, httpserver, fake_or, pinned, settings, clock):
    """ctx whose OpenRouter client talks to the fake server (no paid call is possible)."""
    from misty_engine.jev import JevClient
    ctx.openrouter = OpenRouterClient(
        pinned=pinned, settings=settings, spend=ctx.spend, net=ctx.net,
        key_provider=lambda: "sk-or-v1-test", clock=clock, base_url=httpserver.url_for("/api/v1"))
    ctx.jev = JevClient(ctx.openrouter, pinned, ctx.card)
    from misty_engine.wiki.store import WikiStore
    ctx.wiki = WikiStore(ctx.paths.wiki, clock)
    ctx.wiki.init()
    return ctx


@pytest.fixture
def seed(fctx):
    """seed(item_id, text, ...) inserts a scrubbed item with its chunks and vectors."""
    from misty_engine.chunk import chunk_text

    def make(item_id: str, text: str, *, source: str = "folders", kind: str = "file", title: str = "doc.md",
             status: str = "scrubbed") -> str:
        fctx.con.execute(
            "INSERT OR REPLACE INTO items(id, source, kind, locator, title, status, word_count, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (item_id, source, kind, f"/x/{item_id}", title, status, len(text.split()), fctx.clock.now()))
        chunks = chunk_text(f"{title}\n{text}")
        fctx.index.replace_item_chunks(item_id, source, chunks, fctx.embedder.embed_documents(chunks))
        return item_id
    return make


@pytest.fixture
def ctx_runner(tmp_path, monkeypatch, pinned):
    """Factory: `ctx_runner(*script)` -> (ctx, runner). Every call builds a FRESH engine home (own db, own wiki)
    whose model runner is a ScriptedRunner, so a test can compare several independent runs."""
    import itertools
    counter = itertools.count()

    def build(*script):
        from fakes import FakeEmbedder, ScriptedRunner
        from misty_engine.clock import FakeClock
        from misty_engine.ctx import Ctx
        from misty_engine.index import Index
        from misty_engine.jobs import JobQueue
        from misty_engine.wiki.store import WikiStore
        home = tmp_path / f"run{next(counter)}"
        monkeypatch.setenv("MISTY_HOME", str(home))
        paths = MistyPaths.from_env()
        paths.ensure()
        clk = FakeClock(1_792_000_000.0)
        c = connect(paths.db)
        migrate(c, 768)
        st = Settings(c)
        st.set("folders", [])
        st.set("sources_enabled", {k: False for k in ("folders", "repos", "claude_mem", "graphify", "gmail",
                                                      "gcal", "imessage", "whatsapp")})
        spend, net = SpendMeter(c, clk, st), NetState()
        orc = OpenRouterClient(pinned=pinned, settings=st, spend=spend, net=net,
                               key_provider=lambda: "sk-or-v1-test", clock=clk)
        cx = Ctx(paths=paths, pinned=pinned, con=c, settings=st, queue=JobQueue(c, clk, st), spend=spend, net=net,
                 clock=clk, openrouter=orc, embedder=FakeEmbedder(), index=Index(c, 768))
        cx.wiki = WikiStore(paths.wiki, clk)
        cx.wiki.init()
        cx.runner = ScriptedRunner(*script)
        return cx, cx.runner
    return build


@pytest.fixture
def make_items():
    """make_items(ctx, [(item_id, meta[, extra])]): embedded items with a source note, ready for the resolve sweep.
    `extra` may carry title, source, kind, modified_at, summary (row/note fields); everything else joins meta."""
    NOTE = ("---\ntype: source\ntitle: {t}\nitem_id: {i}\nsource: {s}\nupdated: 2026-10-14\n---\n\n"
            "## Summary\n{sm}\n\n## Key facts\n- f\n\n## People\n- (none)\n\n## Links\n- (none)\n")

    def make(ctx, specs, status="embedded"):
        import json as _json
        out = []
        for spec in specs:
            iid, meta = spec[0], dict(spec[1])
            extra = dict(spec[2]) if len(spec) > 2 else {}
            title = extra.pop("title", f"Note {iid}")
            source = extra.pop("source", "whatsapp" if "chat_id" in meta or "chat_kind" in extra else "gmail")
            kind = extra.pop("kind", "chat_week" if source in ("whatsapp", "imessage") else "mail_thread")
            mod = extra.pop("modified_at", "2026-10-12T00:00:00+00:00")
            summary = extra.pop("summary", f"Summary of {title}.")
            meta.update(extra)
            meta.setdefault("people", [])
            meta.setdefault("projects", [])
            meta.setdefault("topics", [])
            meta.setdefault("dated_lines", [])
            path = f"sources/{source}/{iid[-6:]}.md"
            (ctx.paths.wiki / path).parent.mkdir(parents=True, exist_ok=True)
            (ctx.paths.wiki / path).write_text(NOTE.format(t=title, i=iid, s=source, sm=summary))
            ctx.con.execute(
                "INSERT OR REPLACE INTO items(id, source, kind, locator, title, status, note_path, modified_at, "
                "meta, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (iid, source, kind, f"/x/{iid}", title, status, path, mod, _json.dumps(meta, sort_keys=True),
                 ctx.clock.now()))
            out.append(iid)
        return out
    return make
