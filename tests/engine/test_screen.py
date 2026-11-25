"""Step C4: read_screen. Accessibility first, OCR when thin, 6,000 character cap centred on the selection, private apps refused,
text scrubbed, and history keeps one short line, never the text."""
import json

import pytest

from fakes_hands import ScreenHands
from misty_engine import chat, screen

LONG = "Covalent bonds share electrons between atoms. " * 12          # over 200 characters


def put(ctx, hands):
    ctx.extras = {**(ctx.extras or {}), "hands": hands}


@pytest.mark.asyncio
async def test_accessibility_text_when_it_is_enough(ctx):
    h = ScreenHands(ax={"app": "Pages", "title": "Chem", "text": LONG})
    put(ctx, h)
    r = await screen.read(ctx)
    assert r["ok"] and r["how"] == "accessibility" and r["app"] == "Pages" and not r["cut"]
    assert h.calls == ["read_window_text"]


@pytest.mark.asyncio
async def test_thin_text_falls_back_to_ocr(ctx):
    h = ScreenHands(ax={"app": "Code", "text": "x = 1"}, ocr={"app": "Code", "text": LONG})
    put(ctx, h)
    r = await screen.read(ctx)
    assert r["how"] == "ocr" and r["text"].startswith("Covalent") and h.calls == ["read_window_text", "ocr_window"]


@pytest.mark.asyncio
async def test_threshold_is_200_characters(ctx):
    h = ScreenHands(ax={"text": "a" * 200}, ocr={"text": LONG})
    put(ctx, h)
    assert (await screen.read(ctx))["how"] == "accessibility"
    h2 = ScreenHands(ax={"text": "a" * 199}, ocr={"text": LONG})
    put(ctx, h2)
    assert (await screen.read(ctx))["how"] == "ocr"


@pytest.mark.asyncio
async def test_no_screen_recording_keeps_the_thin_text(ctx):
    put(ctx, ScreenHands(ax={"text": "short but real"}, fail={"ocr_window": "SCREEN_RECORDING_DENIED"}))
    r = await screen.read(ctx)
    assert r["ok"] and r["how"] == "accessibility" and r["text"] == "short but real"


@pytest.mark.asyncio
async def test_cap_centres_on_the_selection_marker(ctx):
    text = "a" * 20000 + "⟦HERE⟧" + "b" * 20000
    put(ctx, ScreenHands(ax={"text": text}))
    r = await screen.read(ctx, "selection")
    assert len(r["text"]) == screen.MAX_CHARS and "⟦HERE⟧" in r["text"] and r["cut"]
    top = await screen.read(ctx, "top")
    assert top["text"].startswith("aaa") and "HERE" not in top["text"]


@pytest.mark.asyncio
async def test_private_app_is_refused(ctx):
    h = ScreenHands(ax={"private": True})
    put(ctx, h)
    r = await screen.read(ctx)
    assert not r["ok"] and "private" in r["error"] and h.calls == ["read_window_text"]


@pytest.mark.asyncio
async def test_errors_become_plain_reasons_and_secrets_are_scrubbed(ctx):
    put(ctx, ScreenHands(fail={"read_window_text": "NOT_TRUSTED"}))
    assert "Accessibility" in (await screen.read(ctx))["error"]
    put(ctx, ScreenHands(ax={"text": "key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH " + LONG}))
    assert "abcdefghijklmnop" not in (await screen.read(ctx))["text"]
    ctx.extras["hands"] = None
    assert not (await screen.read(ctx))["ok"]


def test_tool_count_is_31_and_unfollow_is_folded_in():
    names = [t["function"]["name"] for t in chat.TOOLS]
    assert len(names) == 33 and "read_screen" in names and "unfollow" not in names


@pytest.mark.asyncio
async def test_follow_stop_still_stops(ctx):
    r = await chat.run_tool(ctx, "follow", {"action": "stop", "key": "nothing"}, on_status=lambda s: _nop(), confirm=None)
    assert r["ok"] is False and "not following" in r["error"]


async def _nop():
    return None


@pytest.mark.asyncio
async def test_history_keeps_one_short_line_never_the_text(ctx):
    from test_chat import ScriptBrain, call, nothing, no
    put(ctx, ScreenHands(ax={"app": "Pages", "title": "Chem", "text": "UNIQUE-SCREEN-TEXT " + LONG}))
    ctx.openrouter.post = ScriptBrain({"calls": [call("read_screen", around="top")]}, {"content": "It is about bonds."}).post
    await chat.send(ctx, "what does the rest of this say", on_delta=nothing, on_status=nothing, confirm=no)
    rows = ctx.con.execute("SELECT text, meta FROM chat_messages").fetchall()
    blob = " ".join((r["text"] or "") + (r["meta"] or "") for r in rows)
    assert "read his screen: Pages" in blob and "UNIQUE-SCREEN-TEXT" not in blob
