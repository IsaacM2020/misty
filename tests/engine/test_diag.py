import json
import sqlite3

import pytest

from fixtures.build_chatdb import build_addressbook, build_chatdb, typedstream_blob
from misty_engine.diag import imessage_stats, title_kind
from misty_engine.sources.contacts import ContactsResolver
from test_whatsapp import wadb  # noqa: F401  (fixture)


@pytest.fixture
def chatdb(tmp_path):
    return build_chatdb(tmp_path / "chat.db")


def _add(path, rowid, chat, text, blob):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO message(ROWID, guid, text, date, is_from_me, handle_id, attributedBody) "
              "VALUES (?,?,?,?,?,?,?)", (rowid, f"g{rowid}", text, 780_000_000_000_000_000, 0, 1, blob))
    c.execute("INSERT INTO chat_message_join VALUES (?,?)", (chat, rowid))
    c.commit()
    c.close()


def test_title_kinds():
    assert title_kind("+65 9123 4567") == "raw_phone"
    assert title_kind("friend@example.com") == "raw_email"
    assert title_kind("Group chat") == "unnamed_group"
    assert title_kind("Arjun Rao") == "named"


def test_stats_on_fixture_db(chatdb):
    s = imessage_stats(chatdb)
    assert s["rows"] == 7 and s["breakdown"]["text_column"] == 4 and s["breakdown"]["blob_decoded"] == 2
    assert s["breakdown"]["no_body"] == 1                  # the empty attachment row
    assert s["with_body"] == 6 and s["usable_text"] == 6 and s["usable_pct"] == 100.0
    assert s["chats"] == 2 and s["chat_titles"] == {"raw_phone": 1, "named": 1}
    assert s["contacts"] == {"loaded": False}


def test_stats_flags_undecodable_rows(chatdb):
    _add(chatdb, 20, 1, None, b"\x04\x0bstreamtyped no class name here")          # marker missing
    _add(chatdb, 21, 1, None, typedstream_blob("￼"))                         # attachment placeholder only
    _add(chatdb, 22, 1, None, typedstream_blob(""))                               # empty string
    s = imessage_stats(chatdb)
    b = s["breakdown"]
    assert b["blob_no_marker"] == 1 and b["blob_object_char_only"] == 1 and b["blob_empty_text"] == 1
    assert s["with_body"] == 9 and s["usable_text"] == 6 and s["usable_pct"] == round(100 * 6 / 9, 1)


def test_stats_carry_no_text_or_names(chatdb, tmp_path):
    book = ContactsResolver(build_addressbook(tmp_path / "AddressBook").parent.parent.parent)
    dumped = json.dumps(imessage_stats(chatdb, book))
    for secret in ("Arjun", "Rao", "hey are we", "iGEM", "friend@example.com", "6591234567", "The Friend"):
        assert secret not in dumped
    assert '"phones": 1' in dumped and '"emails": 1' in dumped and '"dbs_found": 1' in dumped


def test_contacts_reads_top_level_db_and_counts_failures(tmp_path):
    ab = tmp_path / "AddressBook"
    build_addressbook(ab)
    (ab / "AddressBook-v22.abcddb").write_bytes(b"not a database")          # corrupt local-contacts file
    r = ContactsResolver(ab)
    assert r.dbs_found == 2 and r.dbs_failed == 1
    assert r.name_for("+6591234567") == "Arjun Rao"


async def test_dormant_chat_titles_pick_up_contacts_names(ctx, chatdb, tmp_path):
    """Items discovered before Contacts could be read keep a phone number as title until a retitle pass."""
    from misty_engine.pipeline import Worker
    from misty_engine.sources.imessage import IMessageSource

    async def discover(src):
        ctx.sources = {"imessage": src}
        ctx.queue.enqueue("discover", payload={"source": "imessage"})
        await Worker(ctx, {"discover": 1}).run_until_idle(["discover"])

    await discover(IMessageSource(chatdb))
    titles = {r[0] for r in ctx.con.execute("select title from items where source='imessage'")}
    assert "+6591234567 · 2026-W41" in titles
    book = ContactsResolver(build_addressbook(tmp_path / "AddressBook").parent.parent.parent)
    await discover(IMessageSource(chatdb, contacts=book))          # cursor is at the end: nothing new to read
    titles = {r[0] for r in ctx.con.execute("select title from items where source='imessage'")}
    assert "Arjun Rao · 2026-W41" in titles and "+6591234567 · 2026-W41" not in titles
    assert "The Friend · 2026-W42" in titles


def test_attachment_placeholder_is_not_chat_text(chatdb):
    from misty_engine.sources.imessage import IMessageSource
    _add(chatdb, 30, 1, None, typedstream_blob("￼"))
    _add(chatdb, 31, 1, None, typedstream_blob("see ￼ this"))
    src = IMessageSource(chatdb)
    items = list(src.discover(None))
    text = "\n".join(src.load_text(i) for i in items)
    assert len(items) == 5                     # the 2025 week holds only the real text, the bare placeholder is gone
    assert "￼" not in text and "see  this" in text


def test_odd_rows_are_reported_by_structure_only(chatdb):
    _add(chatdb, 40, 1, None, typedstream_blob(""))
    s = imessage_stats(chatdb)
    assert list(s["odd_rows"].values()) == [1]
    key = next(iter(s["odd_rows"]))
    assert "lenbyte=0x00" in key and "balloon=None" in key


def test_whatsapp_stats_recognised_schema(wadb):
    from misty_engine.diag import whatsapp_stats
    s = whatsapp_stats(wadb)
    assert s["schema"] == "ok" and s["chats"] == 3 and s["groups"] == 1 and s["chats_with_name"] == 3
    assert s["messages"] == 6 and s["messages_with_text"] == 5 and s["text_pct"] == 83.3
    assert s["group_member_names"] is True
    dumped = json.dumps(s)
    assert "Arjun" not in dumped and "whatsapp.net" not in dumped and "iGEM" not in dumped


def test_whatsapp_stats_unrecognised_schema_names_what_is_missing(tmp_path):
    from misty_engine.diag import whatsapp_stats
    p = tmp_path / "ChatStorage.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT)")
    c.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZTEXT TEXT)")
    c.commit()
    c.close()
    s = whatsapp_stats(p)
    assert s["schema"] == "unrecognized" and "ZWAMESSAGE" in s["tables_zwa"]
    assert s["missing"]["ZWACHATSESSION"]["missing"] == ["ZLASTMESSAGEDATE", "ZPARTNERNAME"]
    assert "ZISFROMME" in s["missing"]["ZWAMESSAGE"]["missing"]


def test_whatsapp_stats_missing_file(tmp_path):
    from misty_engine.diag import whatsapp_stats
    assert whatsapp_stats(tmp_path / "nope.sqlite") == {"exists": False, "access": False}


def test_photos_stats_counts_originals_only(tmp_path):
    from misty_engine.diag import photos_stats
    lib = tmp_path / "Photos Library.photoslibrary"
    (lib / "originals/0").mkdir(parents=True)
    (lib / "originals/A").mkdir()
    (lib / "resources/derivatives").mkdir(parents=True)
    for name in ("0/a.HEIC", "0/b.jpg", "A/c.jpg", "A/clip.mov"):
        (lib / "originals" / name).write_bytes(b"x" * 1000)
    (lib / "resources/derivatives/thumb.jpg").write_bytes(b"x")          # never counted
    s = photos_stats(lib)
    assert s["images"] == 3 and s["by_ext"] == {".heic": 1, ".jpg": 2} and s["other_files"] == 1
    assert s["est_minutes"] == round(3 / 1.7 / 60, 1) and s["over_size_cap"] == 0
    assert photos_stats(tmp_path / "missing") == {"exists": False}
    dumped = json.dumps(s)
    assert "a.HEIC" not in dumped and str(tmp_path) not in dumped
