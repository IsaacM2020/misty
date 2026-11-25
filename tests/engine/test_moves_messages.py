"""D5: the right person on the right channel, iMessage only on his yes and proved, WhatsApp only filled in, mail only drafted.
No test ever sends a message, opens an app or touches the real Messages database."""
import json

import pytest

from misty_engine import moves
from misty_engine.moves import messages, people
from misty_engine.sources import google_auth

# (name, channel, phone or email, week he last talked to them there)
FOLK = [
    ("Shipra Kumar", "whatsapp", "+6591110001", "2026-W46"),
    ("Shipra Kumar", "imessage", "+6591110001", "2026-W40"),
    ("Mum", "imessage", "+6591110002", "2026-W46"),
    ("Mum", "whatsapp", "+6591110002", "2026-W45"),
    ("Dad", "imessage", "dad@example.com", "2026-W44"),
    ("Aarav Shah", "whatsapp", "+6591110003", "2026-W46"),
    ("Aarav Mehta", "whatsapp", "+6591110004", "2026-W41"),
    ("Rohan Lee", "whatsapp", "+6591110005", "2026-W39"),
    ("Ms Lim", "imessage", "+6591110006", "2026-W30"),
    ("Coach Daniel", "whatsapp", "+6591110007", "2026-W46"),
    ("Coach Daniel", "imessage", "+6591110007", "2026-W46"),
    ("Priya Nair", "imessage", "+6591110008", "2026-W43"),
]


@pytest.fixture
def folk(ctx):
    for i, (name, ch, h, wk) in enumerate(FOLK):
        kind = "email" if "@" in h else "phone"
        meta = {"week": wk, "counterpart": {"name": name, "handle": [kind, h], "from": "contacts"}}
        ctx.con.execute("INSERT INTO items(id, source, kind, locator, title, status, word_count, updated_at, meta) VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"c{i}", ch, "chat_week", f"/c{i}", name, "scrubbed", 10, 0, json.dumps(meta)))
    ctx.con.execute("INSERT INTO items(id, source, kind, locator, title, status, word_count, updated_at, meta) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("g1", "whatsapp", "chat_week", "/g1", "group", "scrubbed", 10, 0,
                     json.dumps({"week": "2026-W46", "chat_id": "1203@g.us", "people": ["Shipra Kumar"]})))
    ctx.con.commit()
    return ctx


@pytest.fixture
def shell(monkeypatch):
    ran = []

    async def ex(*argv, timeout=30.0):
        ran.append(argv)
        return 0, ""
    monkeypatch.setattr(messages, "_exec", ex)
    return ran


def asker(answer=True):
    asked = []

    async def c(q):
        asked.append(q)
        return answer
    return c, asked


# the scored check: 15 ways he names someone -> (person, the channel he used with them last)
USUAL = [("Shipra", "Shipra Kumar", "whatsapp"), ("shipra kumar", "Shipra Kumar", "whatsapp"), ("Mum", "Mum", "imessage"),
         ("mum", "Mum", "imessage"), ("Dad", "Dad", "imessage"), ("Aarav Shah", "Aarav Shah", "whatsapp"),
         ("Aarav Mehta", "Aarav Mehta", "whatsapp"), ("Rohan", "Rohan Lee", "whatsapp"), ("Ms Lim", "Ms Lim", "imessage"),
         ("Coach", "Coach Daniel", "imessage"), ("coach daniel", "Coach Daniel", "imessage"), ("Priya", "Priya Nair", "imessage"),
         ("Lim", "Ms Lim", "imessage"), ("rohan lee", "Rohan Lee", "whatsapp"), ("Priya Nair", "Priya Nair", "imessage")]


@pytest.mark.parametrize("who,name,channel", USUAL, ids=[u[0] for u in USUAL])
def test_d5_scored_right_person_and_usual_channel(folk, who, name, channel):
    assert people.usual(folk, who) == (name, channel)


def test_two_people_called_the_same_and_unknowns_and_groups(folk):
    who, why = people.pick(folk, "Aarav", "whatsapp")
    assert who is None and "Aarav Shah" in why and "Aarav Mehta" in why and "which" in why
    assert "doesn't know who" in people.pick(folk, "Zed", "imessage")[1]
    assert "never a group" in people.pick(folk, "Shipra and Mum", "imessage")[1]
    assert "never a group" in people.pick(folk, "the group", "whatsapp")[1]
    who, why = people.pick(folk, "Rohan", "imessage")
    assert who is None and "on whatsapp" in why                     # no iMessage for Rohan: says where he is


@pytest.mark.asyncio
async def test_imessage_asks_with_full_name_sends_on_yes_and_proves_it(folk, shell, monkeypatch):
    monkeypatch.setattr(messages, "sent_texts", lambda h, since: ["I'll be late"] if h == "+6591110001" else [])
    c, asked = asker()
    r = await moves.run(folk, "message_imessage", {"to": "Shipra", "text": "I'll be late"}, "m1", c)
    assert asked == ['Send to Shipra Kumar on iMessage: "I\'ll be late"?']
    assert r["ok"] and r["verified"] and shell[0][0] == "osascript" and shell[0][-2:] == ("+6591110001", "I'll be late")


@pytest.mark.asyncio
async def test_imessage_no_or_silence_sends_nothing(folk, shell, monkeypatch):
    monkeypatch.setattr(messages, "sent_texts", lambda h, since: [])
    c, _ = asker(False)
    r = await moves.run(folk, "message_imessage", {"to": "Mum", "text": "hi"}, "m2", c)
    assert r["declined"] and r["say"] == "Okay, not sent." and shell == []


@pytest.mark.asyncio
async def test_imessage_not_found_in_the_database_is_not_proved(folk, shell, monkeypatch):
    monkeypatch.setattr(messages, "sent_texts", lambda h, since: [])
    monkeypatch.setattr(messages, "VERIFY_S", 0.0)
    c, _ = asker()
    r = await moves.run(folk, "message_imessage", {"to": "Mum", "text": "hi"}, "m3", c)
    assert r["ok"] and not r["verified"] and "do not say it worked" in r["note"]


@pytest.mark.asyncio
async def test_imessage_from_text_she_read_says_so_in_the_preview(folk, shell, monkeypatch):
    monkeypatch.setattr(messages, "sent_texts", lambda h, since: [])
    c, asked = asker(False)
    await moves.run(folk, "message_imessage", {"to": "Mum", "text": "the code is 4411"}, "m4", c, source="text")
    assert "came from text" in asked[0] and shell == []


@pytest.mark.asyncio
async def test_imessage_unknown_person_never_reaches_messages(folk, shell):
    c, _ = asker()
    r = await moves.run(folk, "message_imessage", {"to": "Zed", "text": "hi"}, "m5", c)
    assert not r["ok"] and shell == []


@pytest.mark.asyncio
async def test_whatsapp_only_fills_in_and_says_not_sent(folk, shell):
    r = await moves.run(folk, "message_whatsapp", {"to": "Shipra", "text": "I'll be late"}, "w1")
    assert r["ok"] and r["verified"] and "NOT sent" in r["what"] and r["sent"] is False
    assert shell[0][0] == "open" and shell[0][1].startswith("whatsapp://send?phone=6591110001&text=I%27ll+be+late")


class FakeDrafts:
    def __init__(self):
        self.made = []

    def users(self):
        return self

    def drafts(self):
        return self

    def create(self, userId, body):
        self.made.append(body)
        return _R({"id": f"d{len(self.made)}", "message": {"id": "m1"}})

    def get(self, userId, id):
        return _R({"id": id})


class _R:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


@pytest.mark.asyncio
async def test_mail_draft_is_a_draft_to_the_right_address_and_never_sent(folk, monkeypatch):
    import base64
    from misty_engine import chat
    g = FakeDrafts()
    opened = []

    async def op(t):
        opened.append(t)
    monkeypatch.setattr(messages, "_gmail", lambda: g)
    monkeypatch.setattr(chat, "_open", op)
    monkeypatch.setattr(google_auth, "granted", lambda: set(google_auth.SCOPES))
    r = await moves.run(folk, "mail_draft", {"to": "Dad", "subject": "Pickup", "body": "Can you get me at 6?"}, "e1")
    assert r["ok"] and r["verified"] and "NOT sent" in r["what"] and opened and "drafts" in opened[0], r
    raw = base64.urlsafe_b64decode(g.made[0]["message"]["raw"]).decode()
    assert "To: dad@example.com" in raw and "Subject: Pickup" in raw
    r = await moves.run(folk, "mail_draft", {"to": "tan@school.edu.sg", "subject": "IA", "body": "x"}, "e2")
    assert r["ok"] and "To: tan@school.edu.sg" in base64.urlsafe_b64decode(g.made[1]["message"]["raw"]).decode()


@pytest.mark.asyncio
async def test_mail_draft_without_the_new_sign_in_says_what_to_do(folk, monkeypatch):
    monkeypatch.setattr(google_auth, "granted", lambda: set(google_auth.READ_SCOPES))
    r = await moves.run(folk, "mail_draft", {"to": "Dad", "subject": "x", "body": "y"}, "e3")
    assert not r["ok"] and "sign in to Google once more" in r["what"]


def test_no_move_can_send_mail():
    from misty_engine.moves import registry
    import inspect
    src = inspect.getsource(messages)
    assert ".send(" not in src.replace("send theText", "") and "messages().send" not in src
    assert all("mail" not in n or n == "mail_draft" for n in registry.MOVES)
