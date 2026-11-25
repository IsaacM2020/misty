"""open_chat_link: the newest https link from one WhatsApp chat, only today's by default, never a payment page, never
an ambiguous chat. A fake ChatStorage.sqlite with WhatsApp's own column names; no link is ever really opened."""
import sqlite3

import pytest

from misty_engine import moves, protocols
from misty_engine.moves import chatlink

APPLE = 978307200
NOW = 1_794_801_600.0                     # Mon 16 Nov 2026, 12:00 SGT


def wa_db(path, msgs):
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT, ZLASTMESSAGEDATE REAL);"
                      "CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZISFROMME INTEGER, ZTEXT TEXT, ZMESSAGEDATE REAL);")
    con.executemany("INSERT INTO ZWACHATSESSION VALUES (?,?,?,0)", [(1, "1@g.us", "Gr 11 Maths - Shipra"), (2, "2@g.us", "Gr 11 Physics"),
                                                                   (3, "3@g.us", "Gr 10 Maths"), (4, "+65@s.whatsapp.net", "Aarav")])
    con.executemany("INSERT INTO ZWAMESSAGE(ZCHATSESSION, ZISFROMME, ZTEXT, ZMESSAGEDATE) VALUES (?,?,?,?)", msgs)
    con.commit()
    con.close()


@pytest.fixture
def wa(ctx, tmp_path, monkeypatch):
    ctx.clock.t = NOW
    path = tmp_path / "ChatStorage.sqlite"
    opened = []

    async def ex(*argv, timeout=20.0):
        opened.append(argv)
        return 0
    monkeypatch.setattr(chatlink, "WA_DB", path)
    monkeypatch.setattr(chatlink, "_exec", ex)
    return path, opened


T = NOW - APPLE                           # "now" in WhatsApp's clock


def test_names_he_would_say_fit_the_group():
    fits = chatlink.name_fits
    assert fits("grade eleven maths shipra", "Gr 11 Maths - Shipra") and fits("grade 11 mathematics", "Grade 11 Math")
    assert fits("11 maths shipra", "Gr 11 Maths - Shipra") and fits("eleven maths", "Gr 11 Maths - Shipra")
    assert fits("the shipra group", "Gr 11 Maths - Shipra") and not fits("physics", "Gr 11 Maths - Shipra")


@pytest.mark.asyncio
async def test_opens_todays_newest_link_in_safari(ctx, wa):
    path, opened = wa
    wa_db(path, [(1, 0, "old link https://meet.google.com/old-old", T - 3 * 86400),
                 (1, 0, "class now! https://zoom.us/j/123?pwd=x.", T - 600),
                 (1, 0, "see you", T - 60), (2, 0, "https://physics.example.com", T)])
    r = await moves.run(ctx, "open_chat_link", {"chat": "shipra", "browser": "safari"}, "l1")
    assert r["ok"] and r["verified"] and "zoom.us" in r["what"] and "pwd" not in r["what"]
    assert opened == [("open", "-a", "Safari", "https://zoom.us/j/123?pwd=x")]


@pytest.mark.asyncio
async def test_nothing_today_opens_nothing_and_says_when_the_last_was(ctx, wa):
    path, opened = wa
    wa_db(path, [(1, 0, "https://meet.google.com/abc", T - 2 * 86400)])
    r = await moves.run(ctx, "open_chat_link", {"chat": "shipra"}, "l2")
    assert not r["ok"] and "today" in r["what"] and "Sat 14 Nov" in r["what"] and opened == []
    r = await moves.run(ctx, "open_chat_link", {"chat": "shipra", "today_only": False}, "l3")
    assert r["ok"] and opened == [("open", "https://meet.google.com/abc")]


@pytest.mark.asyncio
async def test_two_chats_fit_so_she_asks(ctx, wa):
    path, opened = wa
    wa_db(path, [(1, 0, "https://a.com", T), (3, 0, "https://b.com", T)])
    r = await moves.run(ctx, "open_chat_link", {"chat": "maths"}, "l4")
    assert not r["ok"] and "Gr 11 Maths - Shipra" in r["what"] and "Gr 10 Maths" in r["what"] and opened == []


@pytest.mark.asyncio
async def test_no_chat_no_link_payment_page_and_no_whatsapp(ctx, wa, monkeypatch):
    path, opened = wa
    wa_db(path, [(2, 0, "pay here https://shop.example.com/checkout?id=1", T)])
    assert "no WhatsApp chat called" in (await moves.run(ctx, "open_chat_link", {"chat": "chemistry"}, "l5"))["what"]
    assert "no link has been sent" in (await moves.run(ctx, "open_chat_link", {"chat": "shipra"}, "l6"))["what"]
    assert "payment page" in (await moves.run(ctx, "open_chat_link", {"chat": "physics"}, "l7"))["what"]
    monkeypatch.setattr(chatlink, "WA_DB", path.parent / "missing" / "x.sqlite")
    assert "can't read WhatsApp" in (await moves.run(ctx, "open_chat_link", {"chat": "shipra"}, "l8"))["what"]
    assert opened == []


@pytest.mark.asyncio
async def test_launch_shipra_class_as_a_protocol(ctx, wa):
    path, opened = wa
    wa_db(path, [(1, 0, "https://zoom.us/j/999", T - 30)])

    async def quiet(_s): ...

    async def yes(_q): return True
    protocols.save(ctx, "shipra class", ["launch shipra class"],
                   [{"move": "open_chat_link", "params": {"chat": "shipra", "browser": "safari"}},
                    {"move": "timer_start", "params": {"minutes": 60, "label": "maths tuition"}}])
    r = await protocols.run(ctx, protocols.get(ctx.con, "shipra class"), yes, quiet)
    assert r["ok"] and not r["unproved"] and opened == [("open", "-a", "Safari", "https://zoom.us/j/999")]
