import sqlite3

import pytest

from misty_engine.errors import SchemaUnrecognized
from misty_engine.sources.whatsapp import WhatsAppSource
from misty_engine.sources.whatsapp_export import ExportSource, parse_export

APPLE_EPOCH = 978307200
J1, J2, J3 = "6591234567@s.whatsapp.net", "6597654321@s.whatsapp.net", "12036302@g.us"


def apple_s(iso: str) -> float:
    from datetime import datetime, timezone
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() - APPLE_EPOCH


@pytest.fixture
def wadb(tmp_path):
    p = tmp_path / "ChatStorage.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT, ZLASTMESSAGEDATE REAL)")
    c.execute("CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCONTACTNAME TEXT)")
    c.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZISFROMME INTEGER, "
              "ZTEXT TEXT, ZMESSAGEDATE REAL, ZGROUPMEMBER INTEGER)")
    c.execute("INSERT INTO ZWACHATSESSION VALUES (1, ?, 'Arjun', ?), (2, ?, 'Mom', ?), (3, ?, 'NOI group', ?)",
              (J1, apple_s("2026-10-13T09:00:00"), J2, apple_s("2026-10-13T10:00:00"), J3, apple_s("2026-10-13T11:00:00")))
    c.execute("INSERT INTO ZWAGROUPMEMBER VALUES (1, 'Priya')")
    rows = [
        (1, 1, 0, "bro did you finish the iGEM part", "2026-10-05T10:00:00", None),
        (2, 1, 1, "almost, sending tonight", "2026-10-05T10:01:00", None),
        (3, 1, 0, "cool", "2026-10-13T09:00:00", None),
        (4, 2, 0, "dinner at 8", "2026-10-13T10:00:00", None),
        (5, 3, 0, "contest on saturday", "2026-10-13T11:00:00", 1),
        (6, 3, 1, None, "2026-10-13T11:05:00", None),
    ]
    for pk, chat, me, text, iso, gm in rows:
        c.execute("INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?,?)", (pk, chat, me, text, apple_s(iso), gm))
    c.commit()
    c.close()
    return p


def test_only_picked_chats_are_read(wadb):
    src = WhatsAppSource(wadb, picked={J1, J3})
    items = list(src.discover(None))
    assert {i.meta["chat_id"] for i in items} == {J1, J3}
    assert len(items) == 3                              # Arjun: W41 + W42, group: W42
    assert all(i.source == "whatsapp" and i.kind == "chat_week" for i in items)
    assert all(i.locator.startswith(f"whatsapp:{i.meta['chat_id']}:") for i in items)


def test_nothing_picked_nothing_read(wadb):
    assert list(WhatsAppSource(wadb, picked=set()).discover(None)) == []


def test_cursor_and_scope_change(wadb):
    src = WhatsAppSource(wadb, picked={J1})
    list(src.discover(None))
    cur = src.next_cursor()
    assert list(WhatsAppSource(wadb, picked={J1}).discover(cur)) == []
    more = list(WhatsAppSource(wadb, picked={J1, J2}).discover(cur))      # Mom was just picked: full re-read
    assert {i.meta["chat_id"] for i in more} == {J1, J2}                 # J1 repeats; upsert dedupes by rev


def test_load_text_senders(wadb):
    src = WhatsAppSource(wadb, picked={J1, J3})
    by = {(i.meta["chat_id"], i.meta["week"][-3:]): i for i in src.discover(None)}
    t = src.load_text(by[(J1, "W41")])
    assert "Arjun: bro did you finish the iGEM part" in t and "Isaac: almost, sending tonight" in t
    g = src.load_text(by[(J3, "W42")])
    assert "Priya: contest on saturday" in g and g.count("\n") == 0       # empty message skipped


def test_list_chats(wadb):
    chats = {c.chat_id: c for c in WhatsAppSource(wadb, picked=set()).list_chats()}
    assert chats[J1].name == "Arjun" and chats[J1].count == 3 and chats[J1].last_message_at.startswith("2026-10-13")


def test_schema_recognition(wadb, tmp_path):
    WhatsAppSource(wadb, picked=set()).check_schema()
    bad = tmp_path / "bad.sqlite"
    c = sqlite3.connect(bad)
    c.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT, ZLASTMESSAGEDATE REAL)")
    c.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZISFROMME INTEGER, ZMESSAGEDATE REAL)")
    c.commit()
    c.close()
    with pytest.raises(SchemaUnrecognized):                  # ZTEXT is gone
        WhatsAppSource(bad, picked=set()).check_schema()


def test_access_denied_is_false(tmp_path):
    assert WhatsAppSource(tmp_path / "nope.sqlite", picked=set()).check_access() is False


async def test_pipeline_pauses_source_and_asks_for_export(ctx, tmp_path):
    from misty_engine.pipeline import Worker
    bad = tmp_path / "bad.sqlite"
    c = sqlite3.connect(bad)
    c.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY)")
    c.commit()
    c.close()
    ctx.sources = {"whatsapp": WhatsAppSource(bad, picked={J1})}
    ctx.queue.enqueue("discover", payload={"source": "whatsapp"})
    await Worker(ctx, {"discover": 1}).run_until_idle()
    assert ctx.con.execute("select health from sources_state where source='whatsapp'").fetchone()[0] == "needs_export"
    assert ctx.con.execute("select count(*) from jobs where status='failed'").fetchone()[0] == 0


# ---------------------------------------------------------------- export fallback

EXPORT_IOS = """‎[14/10/2026, 21:03:11] Arjun: hey
[14/10/2026, 21:04:02] Isaac: second line coming
this is line two
and line three
[15/10/2026, 09:00:00] Arjun: kya scene hai 🏏
[15/10/2026, 09:01:00] ‎Messages and calls are end-to-end encrypted.
"""

EXPORT_ANDROID = """14/10/2026, 9:03 pm - Messages and calls are end-to-end encrypted.
14/10/2026, 9:03 pm - Arjun: hello there
14/10/2026, 9:04 PM - Isaac: reply
15/10/2026, 07:30 - Arjun: 24h time works too
"""


def test_parse_ios_export_multiline_and_system_lines(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text(EXPORT_IOS)
    msgs = parse_export(p)
    assert [m.sender for m in msgs] == ["Arjun", "Isaac", "Arjun"]
    assert msgs[1].text == "second line coming\nthis is line two\nand line three"
    assert msgs[0].ts == "2026-10-14T21:03:11"
    assert msgs[2].text == "kya scene hai 🏏"


def test_parse_android_export_12h_and_dd_mm(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text(EXPORT_ANDROID)
    msgs = parse_export(p)
    assert [(m.ts, m.sender) for m in msgs] == [
        ("2026-10-14T21:03:00", "Arjun"), ("2026-10-14T21:04:00", "Isaac"), ("2026-10-15T07:30:00", "Arjun")]
    assert msgs[0].text == "hello there"


def test_parse_export_narrow_nbsp_before_pm(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text("[14/10/2026, 9:03:11 PM] Arjun: nbsp time\n")
    assert parse_export(p)[0].ts == "2026-10-14T21:03:11"


def test_parse_export_garbage_is_empty(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text("not a chat\nat all\n")
    assert parse_export(p) == []


def test_export_source_groups_per_week_and_loads(tmp_path):
    p = tmp_path / "WhatsApp Chat with Arjun.txt"
    p.write_text(EXPORT_IOS)
    src = ExportSource([p])
    items = list(src.discover(None))
    assert len(items) == 1 and items[0].kind == "chat_week" and items[0].source == "whatsapp_export"
    assert items[0].locator.startswith("whatsapp-export:") and items[0].title.startswith("WhatsApp Chat with Arjun")
    t = src.load_text(items[0])
    assert "Arjun: hey" in t and "this is line two" in t
    assert list(ExportSource([p]).discover(None))[0].locator == items[0].locator      # stable ids


def test_export_changes_bump_rev(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text(EXPORT_IOS)
    a = list(ExportSource([p]).discover(None))[0]
    p.write_text(EXPORT_IOS + "[15/10/2026, 09:30:00] Isaac: new\n")
    b = list(ExportSource([p]).discover(None))[0]
    assert a.locator == b.locator and a.meta["rev"] != b.meta["rev"]


def test_cli_import_export_stores_path(tmp_path, misty_home):
    from misty_engine.cli import main
    from misty_engine.config import Settings
    from misty_engine.db import connect, migrate
    p = tmp_path / "c.txt"
    p.write_text(EXPORT_IOS)
    assert main(["whatsapp", "import-export", str(p)]) == 0
    con = connect(misty_home.db)
    migrate(con, 768)
    assert Settings(con).get("whatsapp_exports") == [str(p)]
    assert main(["whatsapp", "import-export", str(tmp_path / "missing.txt")]) == 1


def _poison(wadb):
    """Real databases hold odd dates (NULL, absurdly large) on a few chats and messages."""
    c = sqlite3.connect(wadb)
    c.execute("INSERT INTO ZWACHATSESSION VALUES (9, 'weird@s.whatsapp.net', 'Weird dates', 9e11)")
    c.execute("INSERT INTO ZWACHATSESSION VALUES (10, 'nulls@s.whatsapp.net', 'Null dates', NULL)")
    c.execute("INSERT INTO ZWAMESSAGE VALUES (90, 9, 0, 'far future', 9e11, NULL)")
    c.execute("INSERT INTO ZWAMESSAGE VALUES (91, 9, 0, 'sane one', ?, NULL)", (apple_s("2026-10-13T12:00:00"),))
    c.execute("INSERT INTO ZWAMESSAGE VALUES (92, 10, 0, 'no date at all', NULL, NULL)")
    c.commit()
    c.close()


def test_list_chats_survives_unconvertible_dates(wadb):
    _poison(wadb)
    chats = {c.chat_id: c for c in WhatsAppSource(wadb).list_chats()}
    assert len(chats) == 5 and chats["weird@s.whatsapp.net"].last_message_at is None
    assert chats["weird@s.whatsapp.net"].count == 2 and chats["nulls@s.whatsapp.net"].last_message_at is None


def test_discover_and_load_skip_messages_with_unconvertible_dates(wadb):
    _poison(wadb)
    src = WhatsAppSource(wadb, picked={"weird@s.whatsapp.net", "nulls@s.whatsapp.net"})
    items = list(src.discover(None))
    assert [i.meta["week"] for i in items] == ["2026-W42"]            # only the sane message forms a week
    assert src.load_text(items[0]).count("\n") == 0 and "sane one" in src.load_text(items[0])
    assert "far future" not in src.load_text(items[0])


def test_item_meta_has_chat_kind_and_counterpart(wadb):
    src = WhatsAppSource(wadb, picked={J1, J2, J3})
    by_chat = {}
    for i in src.discover(None):
        by_chat[i.meta["chat_id"]] = i.meta
    one = by_chat[J1]
    assert one["chat_kind"] == "1to1"
    assert one["counterpart"] == {"name": "Arjun", "handle": ["phone", "+6591234567"], "from": "chat"}
    grp = by_chat[J3]
    assert grp["chat_kind"] == "group" and "counterpart" not in grp


def test_phone_shaped_partner_name_is_not_a_counterpart_name(wadb):
    c = sqlite3.connect(wadb)
    c.execute("UPDATE ZWACHATSESSION SET ZPARTNERNAME='+65 9123 4567' WHERE Z_PK=1")
    c.commit()
    c.close()
    meta = next(WhatsAppSource(wadb, picked={J1}).discover(None)).meta
    assert meta["counterpart"]["name"] is None and meta["counterpart"]["handle"] == ["phone", "+6591234567"]
