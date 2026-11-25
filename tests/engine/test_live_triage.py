import json

import pytest

from misty_engine import suggest
from misty_engine.pipeline import Worker, upsert_item
from misty_engine.scheduler import Scheduler
from misty_engine.live import newest_mail_message
from misty_engine.types import RawItem

OLD = "OLD MESSAGE FROM LAST WEEK about nothing. "
NEW = "NEW MESSAGE TEXT please reply about the deadline. "


class FakeChatSource:
    name = "imessage"
    item_sources = ("imessage",)

    def new_messages_text(self, item, after_rowid):
        assert after_rowid == 10                         # prev_rowid came from the previous read
        return f"[2026-10-14 10:00] Aarav: {NEW}"

    def load_text(self, item):
        return f"[2026-10-07 10:00] Aarav: {OLD}\n[2026-10-14 10:00] Aarav: {NEW}"


async def run(ctx, kinds=("triage", "embed_note")):
    await Worker(ctx, {k: 1 for k in kinds}).run_until_idle(list(kinds))
    assert ctx.queue.counts().get("failed", 0) == 0


async def sweep(ctx):
    """The 5-minute Haiku sweep: kept items get their note and, if triage wanted one, their card."""
    Scheduler(ctx)._live_compile()
    await run(ctx, ("compile_batch",))


@pytest.fixture
def live(fctx):
    fctx.settings.set("phase", "live")
    return fctx


async def test_chat_triage_sends_only_new_message_and_card(live, fake_or, seed):
    live.sources = {"imessage": FakeChatSource()}
    iid = seed("it_ct00000000000001", OLD + NEW, source="imessage", kind="chat_week", title="Aarav")
    live.con.execute("UPDATE items SET meta=? WHERE id=?", (json.dumps({"chat_id": "c1", "prev_rowid": 10}), iid))
    live.queue.enqueue("triage", item_id=iid, cloud=True)
    await run(live)
    sent = fake_or.last_jev_state
    assert "NEW MESSAGE TEXT" in sent and "OLD MESSAGE FROM LAST WEEK" not in sent and "Isaac, a student" in sent


async def test_triage_action_creates_card_badge_and_local_compile(live, fake_or, seed):
    fake_or.set_answers({"matters": ("yes", 0.9), "action": ("reply", 0.9), "area": ("other", 0.9),
                         "urgency": ("now", 0.9)})
    iid = seed("it_ml00000000000001", "Subject: Deadline\n[2026-10-14] From: Aarav\nCan you send part 3 today? " * 5,
               source="gmail", kind="mail_thread", title="Deadline")
    live.con.execute("UPDATE items SET meta=? WHERE id=?", (json.dumps({"sender": "aarav@x.com"}), iid))
    events = []

    async def notify(m, p):
        events.append(m)
    live.notify = notify
    live.queue.enqueue("triage", item_id=iid, cloud=True)
    await run(live)
    assert live.con.execute("select status from items where id=?", (iid,)).fetchone()[0] == "triaged"
    assert suggest.list_cards(live.con) == [] and not events   # the card waits for Haiku's `do` line
    await sweep(live)
    cards = suggest.list_cards(live.con)
    assert suggest.badge(live.con) and cards[0]["kind"] == "reply" and cards[0]["sender"] == "aarav@x.com"
    assert cards[0]["text"] == f"Do the thing for {iid}" and "suggestion.new" in events
    assert "pending_card" not in json.loads(live.con.execute("select meta from items where id=?", (iid,)).fetchone()[0])


async def test_matters_no_is_index_only(live, fake_or, seed):
    fake_or.set_answers({"matters": ("no", 0.95)})
    iid = seed("it_mn00000000000001", "Your weekly digest is here " * 20, source="gmail", kind="mail_thread")
    live.queue.enqueue("triage", item_id=iid, cloud=True)
    await run(live)
    assert live.con.execute("select status, matters from items where id=?", (iid,)).fetchone()[:] == ("done", 0)
    assert suggest.list_cards(live.con) == []


async def test_duplicate_triage_does_not_duplicate_card(live, fake_or, seed):
    fake_or.set_answers({"matters": ("yes", 0.9), "action": ("task_or_deadline", 0.9), "urgency": ("today", 0.9)})
    iid = seed("it_dp00000000000001", "Submit the form by Friday please " * 10, source="gmail", kind="mail_thread")
    for _ in range(2):
        live.con.execute("UPDATE items SET status='scrubbed' WHERE id=?", (iid,))
        live.queue.enqueue("triage", item_id=iid, cloud=True)
        await run(live, ("triage",))
        await sweep(live)
    assert len(suggest.list_cards(live.con)) == 1


async def test_promo_is_triaged_but_never_compiled(live, fake_or, seed):
    fake_or.set_answers({"matters": ("yes", 0.9), "action": ("none", 0.9)})
    iid = seed("it_pm00000000000001", "Big sale on shoes today only " * 20, source="gmail", kind="mail_promo")
    live.queue.enqueue("triage", item_id=iid, cloud=True)
    await run(live)
    assert live.con.execute("select status from items where id=?", (iid,)).fetchone()[0] == "done"


def test_newest_mail_message_keeps_subject_and_last_block():
    t = "Subject: Plan\n[2026-10-10] From: A\nfirst\n\n[2026-10-12] From: B\nsecond"
    assert newest_mail_message(t) == "Subject: Plan\n[2026-10-12] From: B\nsecond"
    assert newest_mail_message("no blocks") == "no blocks"


def test_upsert_item_records_prev_rowid_for_chats(con, clock):
    raw = lambda last: RawItem("imessage", "imessage:c1:2026-W42", "chat_week", "Aarav",
                               meta={"chat_id": "c1", "last_rowid": last, "rev": last})
    upsert_item(con, raw(10), clock.now())
    assert upsert_item(con, raw(14), clock.now()) is True
    meta = json.loads(con.execute("select meta from items").fetchone()[0])
    assert meta["last_rowid"] == 14 and meta["prev_rowid"] == 10


async def test_old_chat_week_gets_no_card(live, fake_or, seed):
    fake_or.set_answers({"matters": ("yes", 0.9), "action": ("reply", 0.9), "urgency": ("now", 0.9)})
    live.sources = {"imessage": FakeChatSource()}
    iid = seed("it_ow00000000000001", OLD + NEW, source="imessage", kind="chat_week", title="Singtel · 2025-W42")
    live.con.execute("UPDATE items SET meta=? WHERE id=?",
                     (json.dumps({"chat_id": "c1", "prev_rowid": 10, "week": "2025-W42"}), iid))
    live.queue.enqueue("triage", item_id=iid, cloud=True)
    await run(live)
    assert suggest.list_cards(live.con) == []


async def test_kept_chat_waits_for_the_haiku_sweep(live, fake_or, seed):
    fake_or.set_answers({"matters": ("yes", 0.9), "action": ("none", 0.9), "urgency": ("none", 0.9)})
    live.sources = {"imessage": FakeChatSource()}
    iid = seed("it_kc00000000000001", OLD + NEW, source="imessage", kind="chat_week", title="Aarav")
    live.con.execute("UPDATE items SET meta=? WHERE id=?", (json.dumps({"chat_id": "c1", "prev_rowid": 10}), iid))
    live.queue.enqueue("triage", item_id=iid, cloud=True)
    await run(live)
    assert live.con.execute("select status from items where id=?", (iid,)).fetchone()[0] == "triaged"


async def test_haiku_compile_skips_promos_even_if_queued(live, fake_or, seed):
    iid = seed("it_kq00000000000001", "50% off everything " * 30, source="gmail", kind="mail_promo", title="Sale")
    live.con.execute("UPDATE items SET status='triaged' WHERE id=?", (iid,))
    live.queue.enqueue("compile_batch", payload={"item_ids": [iid]}, cloud=True)
    await Worker(live, {"compile_batch": 1}).run_until_idle(["compile_batch"])
    assert not fake_or.chat_bodies


async def _card_case(live, fake_or, seed, iid, kind, meta, text, title="T"):
    fake_or.set_answers({"matters": ("yes", 0.9), "action": ("reply", 0.9), "urgency": ("today", 0.9)})
    live.sources = {"imessage": FakeChatSource()}
    seed(iid, text, source="imessage" if kind == "chat_week" else "gmail", kind=kind, title=title)
    live.con.execute("UPDATE items SET meta=? WHERE id=?", (json.dumps({"prev_rowid": 10, **meta}), iid))
    live.queue.enqueue("triage", item_id=iid, cloud=True)
    await run(live)
    await sweep(live)
    return suggest.list_cards(live.con)


async def test_file_edits_never_make_cards(live, fake_or, seed):
    assert await _card_case(live, fake_or, seed, "it_fe00000000000001", "file", {}, "todo: finish this " * 20) == []


async def test_business_sender_chat_makes_no_card_but_is_still_compiled(live, fake_or, seed):
    iid = "it_bs00000000000001"
    assert await _card_case(live, fake_or, seed, iid, "chat_week", {"chat_id": "any;-;Singtel"}, OLD + NEW) == []
    assert live.con.execute("select status from items where id=?", (iid,)).fetchone()[0] == "compiled"


async def test_person_chat_card_uses_do_line_and_handle_as_sender(live, fake_or, seed):
    iid = "it_pc00000000000001"
    cards = await _card_case(live, fake_or, seed, iid, "chat_week", {"chat_id": "any;-;+6591234567"}, OLD + NEW)
    assert len(cards) == 1 and cards[0]["text"] == f"Do the thing for {iid}" and cards[0]["sender"] == "+6591234567"


async def test_otp_chat_makes_no_card(live, fake_or, seed):
    class OtpChat(FakeChatSource):
        def new_messages_text(self, item, after_rowid):
            return "[2026-10-14 10:00] +6591234567: 482913 is your verification code. Do not share it."
    fake_or.set_answers({"matters": ("yes", 0.9), "action": ("reply", 0.9), "urgency": ("now", 0.9)})
    live.sources = {"imessage": OtpChat()}
    iid = seed("it_ot00000000000001", OLD + NEW, source="imessage", kind="chat_week", title="T")
    live.con.execute("UPDATE items SET meta=? WHERE id=?",
                     (json.dumps({"prev_rowid": 10, "chat_id": "any;-;+6591234567"}), iid))
    live.queue.enqueue("triage", item_id=iid, cloud=True)
    await run(live)
    await sweep(live)
    assert suggest.list_cards(live.con) == []


async def test_empty_do_line_means_no_card(live, fake_or, seed):
    iid = "it_ed00000000000001"
    fake_or.drafts[iid] = {**__import__("fake_openrouter").default_draft(iid), "do": ""}
    assert await _card_case(live, fake_or, seed, iid, "mail_thread", {"sender": "a@x.com"},
                            "Subject: Receipt\n[2026-10-14] From: Shop\nThanks for your order. " * 5) == []


def test_place_card_refreshes_text_of_open_card(con, clock):
    con.execute("INSERT INTO items(id, source, locator, kind, title, meta, status, created_at, updated_at) "
                "VALUES ('it_x','imessage','l','chat_week','t','{}','compiled',0,0)")
    assert suggest.place_card(con, "it_x", "reply", "Reply to Aarav: lab at 3", "today", None, clock)
    assert suggest.place_card(con, "it_x", "reply", "Reply to Aarav: lab moved to 4", "today", None, clock) is None
    assert [c["text"] for c in suggest.list_cards(con)] == ["Reply to Aarav: lab moved to 4"]


def test_card_allowed_rules():
    ok = suggest.card_allowed
    assert ok("mail_thread", {}, "Verification code 1234") and ok("calendar_week", {}, "")
    assert not ok("file", {}, "") and not ok("repo", {}, "") and not ok("mail_promo", {}, "")
    for h in ("Singtel", "Carousell", "611123-P", "72000"):
        assert not ok("chat_week", {"chat_id": f"any;-;{h}"}, "hi")
    for h in ("+6591234567", "friend@example.com"):
        assert ok("chat_week", {"chat_id": f"any;-;{h}"}, "hi")
    assert ok("chat_week", {"chat_id": "any;+;16d6314ba42943319b8a66bc2bd3d0d0"}, "hi")
    assert not ok("chat_week", {"chat_id": "any;-;+6591234567"}, "Your OTP is 4821")
