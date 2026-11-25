"""PRIVACY GATE (must pass to ship). v1: client level. Extended in M3, M4, M5."""
import json

import pytest

from misty_engine.logs import setup_logging
from misty_engine.scrub import is_secret_file, scrub_text

pytestmark = pytest.mark.usefixtures("restore_logging")

CANARIES = {
    "or_key": "sk-or-v1-" + "c4n4ry0" * 7,
    "env_value": "ENVCANARY9f8e7d6c5b4a39281706f5e4d3c2b1a0",
    "password": "Hunter2Canary!",
    "nric": "S1234567D",
    "card": "4111 1111 1111 1111",
}


@pytest.fixture
def canary_tree(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    (root / ".env").write_text(f"OPENROUTER_API_KEY={CANARIES['or_key']}\nOTHER={CANARIES['env_value']}\n")
    body = ("Project notes for the iGEM team. " * 60 +
            f"\nkey {CANARIES['or_key']}\npassword: {CANARIES['password']}\n"
            f"NRIC {CANARIES['nric']} card {CANARIES['card']}\n")
    (root / "notes.md").write_text(body)
    return root


def assert_clean_payloads(httpserver):
    posts = [r for r, _ in httpserver.log if r.method == "POST"]
    assert posts, "gate must observe at least one request"
    for req in posts:
        raw = req.get_data(as_text=True)
        for name, val in CANARIES.items():
            assert val not in raw and val.replace(" ", "") not in raw, f"canary {name} leaked"
        assert json.loads(raw)["provider"]["zdr"] is True


def assert_logs_clean(misty_home, canary_tree):
    text = "".join(p.read_text() for p in misty_home.logs.glob("engine.log*"))
    for val in CANARIES.values():
        assert val not in text
    for line in (canary_tree / "notes.md").read_text().splitlines():
        if len(line) > 8:
            assert line not in text


async def test_gate_v1_client_paths(client, httpserver, canary_tree, misty_home, chat_ok):
    setup_logging(misty_home.logs)
    assert is_secret_file(canary_tree / ".env")  # .env is never read at all
    httpserver.expect_request("/api/v1/chat/completions").respond_with_json(chat_ok)
    scrubbed = scrub_text((canary_tree / "notes.md").read_text()).text
    await client.chat(model_key="haiku", messages=[{"role": "user", "content": scrubbed}], max_tokens=50,
                      job_id="jb_00000000000000aa", phase="build", build_id="bd_000000000001")
    assert_clean_payloads(httpserver)
    assert_logs_clean(misty_home, canary_tree)


async def test_gate_full_build_pipeline(fctx, fake_or, canary_tree, misty_home, clock):
    """discover -> prep -> worth -> compile -> embed_note with canaries in files and mail."""
    from fakes_google import FakeGmail, make_thread
    from misty_engine.build import Build
    from misty_engine.sources.folders import FolderSource
    from misty_engine.sources.gmail import GmailSource
    setup_logging(misty_home.logs)
    mail_body = (f"Hi Isaac, the wiki sync moved to Friday. " * 30 +
                 f"\nverification code 482913\nkey {CANARIES['or_key']}\npassword: {CANARIES['password']}\n")
    gm = FakeGmail([make_thread("g1", subject="Wiki sync", body=mail_body)])
    sources = {"folders": FolderSource([canary_tree], []), "gmail": GmailSource(gm.factory, clock=clock)}
    st = await Build(fctx, sources=sources).run()
    assert st["phase"] == "done" and st["total"] >= 2
    raw = "\n".join(fake_or.all_payload_texts())
    for name, val in CANARIES.items():
        assert val not in raw and val.replace(" ", "") not in raw, f"canary {name} in a cloud payload"
    assert "482913" not in raw
    for (t,) in fctx.con.execute("select text_scrubbed from item_chunks"):
        for val in list(CANARIES.values()) + ["482913"]:
            assert val not in t
    wiki_text = "".join(p.read_text() for p in fctx.paths.wiki.rglob("*.md"))
    for val in list(CANARIES.values()) + ["482913"]:
        assert val not in wiki_text
    env_rows = fctx.con.execute("select id from items where title='.env'").fetchall()
    for r in env_rows:
        assert fctx.con.execute("select count(*) from item_chunks where item_id=? and text_scrubbed like '%OTHER%'",
                                (r[0],)).fetchone()[0] == 0
    for body in fake_or.all_payload_texts():
        assert json.loads(body)["provider"]["zdr"] is True
    assert fctx.con.execute("select count(*) from spend").fetchone()[0] == fake_or.request_count
    text = "".join(p.read_text() for p in misty_home.logs.glob("engine.log*"))
    for val in CANARIES.values():
        assert val not in text
    assert "Wiki sync" not in text and "iGEM team" not in text


async def test_gate_ask_and_synth_paths(fctx, fake_or, canary_tree, misty_home, clock):
    """After a build, stage 5 prompts and the ask prompt carry wiki text only, no canary and no raw chunk text."""
    from fakes import ScriptedRunner
    from fakes_google import FakeGmail, make_thread
    from misty_engine.build import Build
    from misty_engine.sources.folders import FolderSource
    from misty_engine.wiki import synth
    from misty_engine.wiki.ask import ask_wiki
    from misty_engine.sources.gmail import GmailSource
    setup_logging(misty_home.logs)
    mail = ("Wiki sync moved to Friday for the iGEM team. " * 25 +
            f"\npassword: {CANARIES['password']}\nkey {CANARIES['or_key']}\n")
    gm = FakeGmail([make_thread("g1", subject="Wiki sync", body=mail)])
    await Build(fctx, sources={"folders": FolderSource([canary_tree], []),
                               "gmail": GmailSource(gm.factory, clock=clock)}).run()
    claude = ScriptedRunner()
    fctx.runner = claude
    fctx.wiki.write("areas/work.md", {"type": "area", "title": "Work", "aliases": [], "area": None, "importance": 3,
                                      "status": "accepted", "updated": "2026-10-14", "sources": [], "notes": 0},
                    "## Summary\nOne line.\n\n## Now\n\n## Timeline\n\n## Links\n")
    claude.set("areas", {"areas": [{"slug": "work", "summary": "s"}]})
    claude.set("about", {"about": "Isaac is a student."})
    claude.set("priorities", {"priorities": [{"text": "Ship wiki", "area": None}]})
    await synth.run_all(fctx)
    fake_or.chat_text("See [[about-isaac]] and [[priorities]].")
    r = await ask_wiki(fctx, "what is going on with the wiki", job_id=None)
    blobs = claude.prompts + fake_or.all_payload_texts() + [r.answer]
    for blob in blobs:
        for name, val in CANARIES.items():
            assert val not in blob and val.replace(" ", "") not in blob, f"canary {name} reached a prompt"
    for p in fctx.paths.wiki.rglob("*.md"):
        for val in CANARIES.values():
            assert val not in p.read_text()
    # and the runner refuses if a secret ever did land in a wiki page
    from misty_engine.errors import PrivacyViolation
    from misty_engine.runner import ApiRunner, WikiText
    before = fake_or.request_count
    with pytest.raises(PrivacyViolation):
        await ApiRunner(fctx.openrouter, lambda: "live").run(
            "s", [WikiText(f"password: {CANARIES['password']}")], "areas", purpose="areas")
    assert fake_or.request_count == before                     # refused before anything was sent


async def test_gate_live_triage_path(fctx, fake_or, misty_home, clock):
    """New mail with canaries in live phase: Jev sees a trimmed scrubbed excerpt, Haiku (ZDR) the scrubbed text (P36)."""
    from fakes_google import FakeGmail, make_thread
    from misty_engine import suggest
    from misty_engine.pipeline import Worker
    from misty_engine.scheduler import Scheduler
    from misty_engine.sources.gmail import GmailSource
    setup_logging(misty_home.logs)
    fctx.settings.set("phase", "live")
    fake_or.set_answers({"matters": ("yes", 0.9), "action": ("task_or_deadline", 0.9), "urgency": ("now", 0.9)})
    filler = "The iGEM team is preparing the wiki and the part documentation for the deadline. " * 200
    body = (f"Reminder: submit the iGEM form on Friday.\nverification code 482913\npassword: {CANARIES['password']}\n"
            f"key {CANARIES['or_key']}\nNRIC {CANARIES['nric']}\n" + filler + "TAIL-MARKER-ZZ")
    gm = FakeGmail([make_thread("live1", subject="iGEM form", body=body, sender="Aarav <aarav@x.com>")])
    fctx.sources = {"gmail": GmailSource(gm.factory, clock=clock)}
    fctx.queue.enqueue("discover", payload={"source": "gmail"})
    kinds = ["discover", "prep", "triage", "compile_batch", "embed_note", "resolve"]
    w = Worker(fctx, {k: 1 for k in kinds})
    await w.run_until_idle(kinds)
    Scheduler(fctx)._live_compile()                                         # the 5-minute Haiku sweep
    await w.run_until_idle(kinds)
    Scheduler(fctx)._resolve()                                              # the 60 s resolution sweep
    await w.run_until_idle(kinds)
    assert fctx.queue.counts().get("failed", 0) == 0
    cards = suggest.list_cards(fctx.con)
    assert len(cards) == 1 and cards[0]["kind"] == "task" and suggest.badge(fctx.con)
    assert fctx.con.execute("select status from items where source='gmail'").fetchone()[0] == "done"
    raw = "\n".join(fake_or.all_payload_texts())
    for name, val in CANARIES.items():
        assert val not in raw and val.replace(" ", "") not in raw
    assert "482913" not in raw
    jev = "\n".join(json.dumps(b) for b in fake_or.systemone_bodies)
    assert "TAIL-MARKER-ZZ" not in jev                                     # Jev only saw the trimmed excerpt
    assert all(json.loads(b)["provider"]["zdr"] is True for b in fake_or.all_payload_texts())
    assert len(fake_or.chat_bodies) == 1 and "TAIL-MARKER-ZZ" in raw        # one Haiku call with the scrubbed text
    text = "".join(p.read_text() for p in misty_home.logs.glob("engine.log*"))
    for val in CANARIES.values():
        assert val not in text
    assert "iGEM form" not in text and "Aarav" not in text
