import json
import re

import pytest

from misty_engine.api import build_methods
from misty_engine.registry import render
from misty_engine.registry.store import Registry
from misty_engine.search import search
from misty_engine.wiki import summaries
from misty_engine.wiki.ask import ask_wiki
from misty_engine.wiki.context import build_context
from test_render_lint import world  # noqa: F401


def ids_in(prompt):
    return re.findall(r"^id: (en_[0-9a-f]+)$", prompt, re.M)


def echo(prompt):
    return {"entities": [{"id": i, "summary": "A real summary sentence.", "now": []} for i in ids_in(prompt)]}


@pytest.fixture
def chats(world):
    """Aarav appears in a public chat c1 and a private chat cp; Dev only in cp."""
    ctx = world.ctx
    pub, _ = world.item("it_pu00000000a1b1", dated=[("2026-10-10", "CHAT-LINE about the wiki")], chat_id="c1",
                        source="whatsapp", summary="CHAT-SUMMARY about the project.")
    pub2, _ = world.item("it_pu00000000a2b2", dated=[("2026-10-11", "second public line")], chat_id="c1",
                         source="whatsapp")
    prv, _ = world.item("it_pr00000000a3c3", dated=[("2026-10-12", "SECRET-LINE the secret thing")], chat_id="cp",
                        source="whatsapp", summary="SECRET-SUMMARY private matter.")
    prv2, _ = world.item("it_pr00000000a4c4", dated=[("2026-10-13", "SECRET-LINE again")], chat_id="cp",
                         source="whatsapp")
    aarav = world.entity("person", "Aarav Shah", [pub, pub2, prv], summary="Teammate on the wiki.",
                         path="people/aarav-shah.md")
    dev = world.entity("person", "Dev Rao", [prv, prv2], summary="Friend.", path="people/dev-rao.md")
    for e in (aarav, dev):
        render.write_entity(ctx, e)
    world.aarav, world.dev = aarav, dev
    return world


def test_private_only_entity_has_no_page_even_when_active(chats):
    ctx = chats.ctx
    ctx.settings.set("context_private_chats", ["whatsapp:cp"])
    assert render.render_entity(ctx, chats.dev) is None
    assert "SECRET-LINE" not in render.render_entity(ctx, chats.aarav)[1]


@pytest.mark.asyncio
async def test_private_never_in_summary_timeline_pack(chats):
    ctx = chats.ctx
    ctx.settings.set("context_private_chats", ["whatsapp:cp"])
    ctx.runner = type(ctx.runner)(echo)
    ctx.con.execute("UPDATE entity_text SET prompt_v=0")
    await summaries.summarise_batch(ctx, [chats.aarav])
    assert all("SECRET" not in " ".join(c["parts"]) for c in ctx.runner.calls)
    page = ctx.wiki.read("people/aarav-shah.md")
    assert "SECRET" not in page.body and "SECRET" not in json.dumps(page.fm)
    pack = build_context(ctx, "Aarav", 2000)
    assert "SECRET" not in pack.text


@pytest.mark.asyncio
async def test_switch_applies_to_existing_notes_and_pages(chats):
    ctx = chats.ctx
    ctx.runner = type(ctx.runner)(echo)
    assert "SECRET-LINE" in ctx.wiki.read("people/aarav-shah.md").body
    assert ctx.wiki.exists("people/dev-rao.md")
    out = await build_methods(ctx)["chats.set_private"]({"source": "whatsapp", "chat_id": "cp", "private": True})
    assert out == {"private_chats": 1}
    assert "SECRET-LINE" not in ctx.wiki.read("people/aarav-shah.md").body
    assert "CHAT-LINE" in ctx.wiki.read("people/aarav-shah.md").body            # the public chat stays
    # Dev's only sources are private: no page, back to a candidate, nothing of the old text kept
    assert not ctx.wiki.exists("people/dev-rao.md")
    row = ctx.con.execute("SELECT status, path FROM entities WHERE id=?", (chats.dev,)).fetchone()
    assert (row["status"], row["path"]) == ("candidate", None)
    assert ctx.con.execute("SELECT summary FROM entity_text WHERE entity_id=?", (chats.dev,)).fetchone()[0] == ""


@pytest.mark.asyncio
async def test_switch_requeues_summaries_and_flipping_back_restores(chats):
    ctx = chats.ctx
    m = build_methods(ctx)
    ctx.runner = type(ctx.runner)()                         # a model that returns nothing: the summary stays stale
    await m["chats.set_private"]({"source": "whatsapp", "chat_id": "cp", "private": True})
    assert ctx.con.execute("SELECT prompt_v FROM entity_text WHERE entity_id=?", (chats.aarav,)).fetchone()[0] == 0
    assert chats.aarav in summaries.stale_entities(ctx)
    out = await m["chats.set_private"]({"source": "whatsapp", "chat_id": "cp", "private": False})
    assert out == {"private_chats": 0}
    assert (await m["chats.private_list"]({})) == {"private": []}
    ctx.runner = type(ctx.runner)(echo)
    await summaries.summarise_batch(ctx, [chats.dev])         # Dev has two public sources again: page returns
    assert ctx.wiki.exists("people/dev-rao.md")


@pytest.mark.asyncio
async def test_private_list_round_trips(chats):
    ctx = chats.ctx
    ctx.runner = type(ctx.runner)(echo)
    m = build_methods(ctx)
    await m["chats.set_private"]({"source": "imessage", "chat_id": "iMessage;-;+65", "private": True})
    await m["chats.set_private"]({"source": "whatsapp", "chat_id": "cp", "private": True})
    out = await m["chats.private_list"]({})
    assert out == {"private": [{"source": "imessage", "chat_id": "iMessage;-;+65"},
                               {"source": "whatsapp", "chat_id": "cp"}]}


def test_search_excludes_private_only_when_asked(fctx, seed):
    seed("it_sc00000000000001", "SECRET-LINE budget dinner plans for the group", source="whatsapp", kind="chat_week")
    fctx.con.execute("UPDATE items SET meta=? WHERE id='it_sc00000000000001'", (json.dumps({"chat_id": "cp"}),))
    fctx.settings.set("context_private_chats", ["whatsapp:cp"])
    assert search(fctx, "budget dinner plans")                                  # the app's own search keeps it
    assert search(fctx, "budget dinner plans", exclude_private=True) == []


@pytest.mark.asyncio
async def test_ask_prompt_excludes_private_chunks(fctx, fake_or, seed):
    seed("it_sc00000000000002", "SECRET-LINE what Aarav said about the surprise party", source="whatsapp",
         kind="chat_week")
    fctx.con.execute("UPDATE items SET meta=? WHERE id='it_sc00000000000002'", (json.dumps({"chat_id": "cp"}),))
    fctx.settings.set("context_private_chats", ["whatsapp:cp"])
    fake_or.chat_text("Not in your notes.")
    await ask_wiki(fctx, "what did Aarav say about the surprise party", job_id=None)
    assert fake_or.all_payload_texts() and all("SECRET-LINE" not in t for t in fake_or.all_payload_texts())
