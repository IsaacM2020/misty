import sqlite3

import pytest

from fixtures.build_chatdb import LONG, build_addressbook, build_chatdb, typedstream_blob
from misty_engine.errors import SchemaUnrecognized
from misty_engine.sources.contacts import ContactsResolver
from misty_engine.sources.imessage import IMessageSource
from misty_engine.sources.typedstream import apple_ts, decode_attributed_body


def test_decode_short_and_long_blobs():
    assert decode_attributed_body(typedstream_blob("hello 🏏 world")) == "hello 🏏 world"
    assert decode_attributed_body(typedstream_blob(LONG)) == LONG          # 0x81 length form
    big = "x" * 70_000
    assert decode_attributed_body(typedstream_blob(big)) == big             # 0x82 length form


@pytest.mark.parametrize("blob", [None, b"", b"no marker here", b"xxNSString\x01\x94"])
def test_decode_garbage_is_none(blob):
    assert decode_attributed_body(blob) is None


def test_apple_ts_both_units():
    assert apple_ts(0) == "2001-01-01T00:00:00Z"
    assert apple_ts(1_000_000_000 * 1_000_000_000) == apple_ts(1_000_000_000)
    assert apple_ts(782_000_000_000_000_000) .startswith("2025-")


@pytest.fixture
def chatdb(tmp_path):
    return build_chatdb(tmp_path / "chat.db")


def test_discover_groups_per_chat_per_week(chatdb):
    src = IMessageSource(chatdb)
    items = list(src.discover(None))
    assert len(items) == 4      # 2 chats x (ISO week 41, week 42)
    assert all(i.kind == "chat_week" and i.source == "imessage" for i in items)
    weeks = {(i.meta["chat_id"], i.meta["week"]) for i in items}
    assert len(weeks) == len(items)


def test_discover_cursor_and_rerun_is_empty(chatdb):
    src = IMessageSource(chatdb)
    list(src.discover(None))
    cur = src.next_cursor()
    assert cur.endswith(":7")
    assert list(IMessageSource(chatdb).discover(cur)) == []
    # un-excluding / excluding a chat changes the scope, so history is read again from the start
    other = IMessageSource(chatdb, excluded={"iMessage;-;friend@example.com"})
    assert len(list(other.discover(cur))) == 2


def test_new_message_in_old_week_changes_rev_only(chatdb):
    src = IMessageSource(chatdb)
    first = {i.locator: i for i in src.discover(None)}
    c = sqlite3.connect(chatdb)
    c.execute("INSERT INTO message(ROWID, guid, text, date, is_from_me, handle_id) VALUES (8,'g8','late reply',"
              "(SELECT date FROM message WHERE ROWID=5), 0, 2)")
    c.execute("INSERT INTO chat_message_join VALUES (2, 8)")
    c.commit()
    c.close()
    src2 = IMessageSource(chatdb)
    new = list(src2.discover(src.next_cursor()))
    assert len(new) == 1 and new[0].locator in first
    assert new[0].meta["rev"] != first[new[0].locator].meta["rev"]
    text = src2.load_text(new[0])
    assert "dinner at 8?" in text and "late reply" in text          # whole week, not just the new message


def test_excluded_chat_yields_nothing(chatdb):
    src = IMessageSource(chatdb, excluded={"iMessage;-;friend@example.com"})
    items = list(src.discover(None))
    assert items and all("friend@example.com" not in i.locator for i in items)


def test_load_text_lines_and_senders(chatdb, tmp_path):
    book = ContactsResolver(build_addressbook(tmp_path / "AddressBook").parent.parent.parent)
    src = IMessageSource(chatdb, contacts=book)
    items = {i.meta["chat_id"] + i.meta["week"]: i for i in src.discover(None)}
    wk41 = next(i for i in items.values() if "+6591234567" in i.locator and i.meta["week"].endswith("W41"))
    t = src.load_text(wk41)
    assert "Arjun Rao: hey are we meeting for nets" in t
    assert "Isaac: yes saturday 7am" in t
    assert t.splitlines()[0].startswith("2026-10-05 ")
    wk42 = [i for i in items.values() if i.meta["week"].endswith("W42") and "friend@" in i.locator][0]
    t2 = src.load_text(wk42)
    assert "kya scene hai 🏏" in t2 and "Isaac:" in t2


def test_attributed_body_text_is_decoded_and_empty_skipped(chatdb):
    src = IMessageSource(chatdb)
    items = list(src.discover(None))
    big = "\n".join(src.load_text(i) for i in items)
    assert "iGEM wiki deadline is friday" in big and LONG.strip() in big
    assert big.count("\n") + 1 == 6                       # 7 messages, one empty


def test_new_messages_text_for_live_triage(chatdb):
    src = IMessageSource(chatdb)
    item = next(i for i in src.discover(None) if "+6591234567" in i.locator and i.meta["week"].endswith("W42"))
    assert "iGEM wiki" in src.new_messages_text(item, after_rowid=2)
    assert src.new_messages_text(item, after_rowid=4) == ""


def test_list_chats(chatdb):
    chats = {c.chat_id: c for c in IMessageSource(chatdb).list_chats()}
    assert chats["iMessage;-;friend@example.com"].name == "The Friend"
    assert chats["iMessage;-;+6591234567"].count == 4 and chats["iMessage;-;+6591234567"].last_message_at.startswith("2026-10-13")


def test_schema_check_raises_when_attributed_body_missing(tmp_path):
    p = build_chatdb(tmp_path / "old.db", drop_attributed_body=True)
    with pytest.raises(SchemaUnrecognized):
        IMessageSource(p).check_schema()
    IMessageSource(build_chatdb(tmp_path / "new.db")).check_schema()


def test_check_access_false_when_denied(tmp_path, monkeypatch):
    src = IMessageSource(tmp_path / "chat.db")      # file does not exist: sqlite cannot open it
    assert src.check_access() is False

    def deny(*a, **k):
        raise PermissionError("authorization denied")
    monkeypatch.setattr("misty_engine.sources.imessage.sqlite3.connect", deny)
    assert IMessageSource(tmp_path / "x.db").check_access() is False


def test_contacts_resolver(tmp_path):
    ab = tmp_path / "AddressBook"
    build_addressbook(ab)
    r = ContactsResolver(ab)
    assert r.name_for("+6591234567") == "Arjun Rao"
    assert r.name_for("91234567") == "Arjun Rao"                 # last 8 digits
    assert r.name_for("CLUB@example.COM") == "NUS Cricket Club"
    assert r.name_for("+6599999999") is None
    assert ContactsResolver(tmp_path / "missing").name_for("+6591234567") is None


async def test_imessage_pipeline_marks_needs_fda_without_access(ctx, tmp_path):
    from misty_engine.pipeline import Worker
    ctx.sources = {"imessage": IMessageSource(tmp_path / "nope.db")}
    ctx.queue.enqueue("discover", payload={"source": "imessage"})
    await Worker(ctx, {"discover": 1, "prep": 2}).run_until_idle()
    assert ctx.con.execute("select health from sources_state where source='imessage'").fetchone()[0] == "needs_fda"
    assert ctx.con.execute("select count(*) from jobs where status='failed'").fetchone()[0] == 0


async def test_imessage_pipeline_indexes_with_message_scrubbing(ctx, chatdb):
    import sqlite3 as s3
    c = s3.connect(chatdb)
    c.execute("INSERT INTO message(ROWID, guid, text, date, is_from_me, handle_id) VALUES (9,'g9',"
              "'your OTP is 482913 do not share',(SELECT date FROM message WHERE ROWID=6), 0, 2)")
    c.execute("INSERT INTO chat_message_join VALUES (2, 9)")
    c.commit()
    c.close()
    from misty_engine.pipeline import Worker
    ctx.sources = {"imessage": IMessageSource(chatdb)}
    ctx.queue.enqueue("discover", payload={"source": "imessage"})
    await Worker(ctx, {"discover": 1, "prep": 2}).run_until_idle()
    dump = "\n".join(r[0] for r in ctx.con.execute("select text_scrubbed from item_chunks"))
    assert "482913" not in dump and "[REDACTED:otp]" in dump
    assert ctx.con.execute("select count(*) from items where status='scrubbed'").fetchone()[0] == 4


def test_chat_title_uses_contacts_name_for_unnamed_chats(chatdb):
    class Names:
        def name_for(self, h):
            return "Aarav Shah" if h.endswith("91234567") else None
    titles = {i.meta["chat_id"]: i.title for i in IMessageSource(chatdb, contacts=Names()).discover(None)}
    assert titles["iMessage;-;+6591234567"].startswith("Aarav Shah · ")


def test_item_meta_has_chat_kind_and_counterpart(chatdb):
    class Names:
        def name_for(self, h):
            return "Aarav Shah" if h.endswith("91234567") else None
    metas = {i.meta["chat_id"]: i.meta for i in IMessageSource(chatdb, contacts=Names()).discover(None)}
    one = metas["iMessage;-;+6591234567"]
    assert one["chat_kind"] == "1to1"
    assert one["counterpart"] == {"name": "Aarav Shah", "handle": ["phone", "+6591234567"], "from": "contacts"}


def test_unknown_number_has_a_handle_and_no_name(chatdb):
    meta = next(i.meta for i in IMessageSource(chatdb).discover(None) if i.meta["chat_id"].endswith("+6591234567"))
    assert meta["counterpart"]["name"] is None and meta["counterpart"]["handle"] == ["phone", "+6591234567"]


async def test_google_sign_in_lost_marks_needs_auth_instead_of_failing_every_minute(ctx):
    from misty_engine.errors import Fatal
    from misty_engine.pipeline import Worker

    class SignedOut:
        def discover(self, cursor, limit):
            raise Fatal("NEEDS_AUTH")

        def next_cursor(self):
            return None

    ctx.sources = {"gmail": SignedOut()}
    ctx.queue.enqueue("discover", payload={"source": "gmail"})
    await Worker(ctx, {"discover": 1, "prep": 2}).run_until_idle()
    row = ctx.con.execute("select health, error_code from sources_state where source='gmail'").fetchone()
    assert (row[0], row[1]) == ("needs_auth", "NEEDS_AUTH")
    assert ctx.con.execute("select count(*) from jobs where status='failed'").fetchone()[0] == 0


async def test_scheduler_rechecks_full_disk_access_so_a_late_grant_is_picked_up(ctx):
    from misty_engine.pipeline import Worker
    from misty_engine.scheduler import Scheduler

    class Chats:
        allowed = False

        def check_access(self):
            return self.allowed

        def discover(self, cursor, limit):
            return iter(())

        def next_cursor(self):
            return None

    src = Chats()
    ctx.sources = {"imessage": src}
    ctx.queue.enqueue("discover", payload={"source": "imessage"})
    await Worker(ctx, {"discover": 1, "prep": 2}).run_until_idle()
    health = lambda: ctx.con.execute("select health from sources_state where source='imessage'").fetchone()[0]
    assert health() == "needs_fda"
    sched = Scheduler(ctx)
    sched._recheck_access()
    assert ctx.con.execute("select count(*) from jobs where kind='discover' and status='queued'").fetchone()[0] == 0
    src.allowed = True                                         # Isaac grants Full Disk Access later
    sched._recheck_access()
    await Worker(ctx, {"discover": 1, "prep": 2}).run_until_idle()
    assert health() == "ok"

