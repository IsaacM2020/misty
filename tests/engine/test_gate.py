"""The gate (Season 3, Day 5): when she may speak up on her own. The decision is plain code, so the check is a table of
fake situations with the one right answer each (20 of 20), then the real flow: held items, release, cap, ask, options."""
from datetime import datetime

import pytest

from misty_engine import chat, classes, follows, gate, web
from misty_engine.clock import SGT
from misty_engine.gate import Moment, Presence
from test_chat import ScriptBrain, call, say, sys_text
from test_follows import fake_web, judged, make


def sgt(h, m=0, day=9):
    return datetime(2026, 11, day, h, m, tzinfo=SGT).timestamp()


T = sgt(15)                                    # a Monday afternoon


def run(m=None, *, idle=60.0, voice="idle", seen_ago=0.0, now=T, **kw):
    pres = kw.pop("presence", Presence(idle, voice, now - seen_ago))
    a = dict(now=now, presence=pres, in_session=False, told_today=0, last_told_at=None, cap=5, quiet_from=23, quiet_to=7,
             topic_feedback=[], already=False, age_s=0.0)
    a.update(kw)
    return gate.decide(m or Moment("Cricinfo reports play starts at 3pm.", "soon", topic="f1", topic_text="the match"), **a).action


def mom(u):
    return Moment("Cricinfo reports play starts at 3pm.", u, topic="f1", topic_text="the match")


BITE = Moment("Ten minutes on Topic 0 for Physics?", "soon", topic="teach", topic_text="your Physics course", key="bite:co_1:2026-11-09", plain=True)

# 20 fake situations, each with the one right answer
CASES = [
    ("soon, on a break, nothing else going on", dict(m=mom("soon"), idle=60), "say"),
    ("now, a short pause is enough", dict(m=mom("now"), idle=12), "say"),
    ("now but he is typing: silent card", dict(m=mom("now"), idle=3), "card"),
    ("soon while typing waits", dict(m=mom("soon"), idle=3), "wait"),
    ("soon needs a real break, 15 s is not one", dict(m=mom("soon"), idle=15), "wait"),
    ("later waits past a 60 s break", dict(m=mom("later"), idle=60), "wait"),
    ("later is said after a big break", dict(m=mom("later"), idle=150), "say"),
    ("in class: even now waits", dict(m=mom("now"), idle=60, in_session=True), "wait"),
    ("on a call: even now waits", dict(m=mom("now"), idle=60, in_session=True, voice="idle"), "wait"),
    ("quiet hours at 2 am: even now waits", dict(m=mom("now"), idle=60, now=sgt(2)), "wait"),
    ("22:59 is not quiet yet", dict(m=mom("soon"), idle=60, now=sgt(22, 59)), "say"),
    ("23:00 is quiet", dict(m=mom("soon"), idle=60, now=sgt(23)), "wait"),
    ("07:00 is no longer quiet", dict(m=mom("soon"), idle=60, now=sgt(7)), "say"),
    ("the daily limit is reached: even now waits", dict(m=mom("now"), idle=60, told_today=5), "wait"),
    ("one under the limit is fine", dict(m=mom("soon"), idle=60, told_today=4), "say"),
    ("already told this: dropped", dict(m=mom("now"), idle=60, already=True), "drop"),
    ("a soon item 13 hours old is stale: dropped", dict(m=mom("soon"), idle=60, age_s=13 * 3600), "drop"),
    ("two dismissals in a row, recent: backing off", dict(m=mom("now"), idle=60, topic_feedback=[("down", T - 600), ("ignored", T - 900)]), "wait"),
    ("two dismissals but over a day ago: asks first", dict(m=mom("soon"), idle=60,
                                                           topic_feedback=[("down", T - 90000), ("down", T - 95000)]), "ask"),
    ("one dismissal: asks before telling", dict(m=mom("soon"), idle=60, topic_feedback=[("down", T - 3600)]), "ask"),
    ("one dismissal but it is urgent: just tells", dict(m=mom("now"), idle=60, topic_feedback=[("down", T - 3600)]), "say"),
    ("a thumbs-up since the dismissal: tells", dict(m=mom("soon"), idle=60, topic_feedback=[("up", T - 60), ("down", T - 3600)]), "say"),
    ("told something 30 s ago: waits", dict(m=mom("soon"), idle=60, last_told_at=T - 30), "wait"),
    ("the app has not reported: she cannot tell, waits", dict(m=mom("now"), presence=None), "wait"),
    # E15: the daily study bite is an ordinary "soon" line, so every hard rule holds for it
    ("study bite, a quiet break: said", dict(m=BITE, idle=60), "say"),
    ("study bite while a class is running: waits", dict(m=BITE, idle=60, in_session=True), "wait"),
    ("study bite while he is on a call or talking: waits", dict(m=BITE, idle=60, voice="speaking"), "wait"),
    ("study bite in quiet hours: waits", dict(m=BITE, idle=60, now=sgt(23, 30)), "wait"),
    ("study bite at the daily limit: waits", dict(m=BITE, idle=60, told_today=5), "wait"),
    ("study bite already asked today (the one-a-day key): dropped", dict(m=BITE, idle=60, already=True), "drop"),
    ("the last report is 100 s old: stale, waits", dict(m=mom("now"), idle=60, seen_ago=100), "wait"),
    ("he is talking with her: waits", dict(m=mom("now"), idle=60, voice="listening"), "wait"),
    # D2: a timer he set himself (own)
    ("his own timer while he types: said", dict(m=Moment("Your timer is up.", "now", topic="timer", own=True), idle=3), "say"),
    ("his own timer in class: waits", dict(m=Moment("Your timer is up.", "now", topic="timer", own=True), idle=3, in_session=True), "wait"),
    ("his own timer at night: waits", dict(m=Moment("Your timer is up.", "now", topic="timer", own=True), idle=60, now=sgt(23, 30)), "wait"),
    ("his own timer skips the daily limit", dict(m=Moment("Your timer is up.", "now", topic="timer", own=True), idle=60, told_today=5), "say"),
    ("a news item that is not his own still waits at the limit", dict(m=mom("now"), idle=60, told_today=5), "wait"),
    # D9: a routine (an ordinary "soon" line from her own routine: no exceptions)
    ("a routine in class: waits", dict(m=Moment("Class is over. Run your routine?", "soon", topic="routine", plain=True), idle=60, in_session=True), "wait"),
    ("a routine at night: waits", dict(m=Moment("It's 07:30. Run your routine?", "soon", topic="routine", plain=True), idle=60, now=sgt(23, 30)), "wait"),
    ("a routine while he types: waits (no typing exception)", dict(m=Moment("Class is over. Run your routine?", "soon", topic="routine", plain=True), idle=3), "wait"),
    ("a routine while he talks with her: waits", dict(m=Moment("Class is over. Run your routine?", "soon", topic="routine", plain=True), idle=60, voice="speaking"), "wait"),
    ("a routine on a break: said", dict(m=Moment("Class is over. Run your routine?", "soon", topic="routine", plain=True), idle=60), "say"),
]


def test_there_are_at_least_twenty_situations():
    assert len(CASES) >= 20


@pytest.mark.parametrize("name,kw,want", CASES, ids=[c[0] for c in CASES])
def test_the_gate_decides_each_fake_situation_correctly(name, kw, want):
    assert run(**kw) == want, name


def test_every_decision_has_a_plain_reason():
    m = mom("soon")
    for kw in (dict(idle=60), dict(idle=3), dict(in_session=True), dict(told_today=9), dict(already=True)):
        a = dict(now=T, presence=Presence(kw.pop("idle", 60), "idle", T), in_session=False, told_today=0, last_told_at=None,
                 cap=5, quiet_from=23, quiet_to=7, topic_feedback=[], already=False)
        a.update(kw)
        assert gate.decide(m, **a).why


def test_quiet_hours_wrap_midnight_and_can_be_off():
    assert gate.is_quiet(23, 23, 7) and gate.is_quiet(0, 23, 7) and gate.is_quiet(6, 23, 7) and not gate.is_quiet(7, 23, 7)
    assert gate.is_quiet(13, 12, 14) and not gate.is_quiet(14, 12, 14)
    assert not any(gate.is_quiet(h, 5, 5) for h in range(24))


# ---- the real flow

@pytest.fixture
def g(ctx):
    ctx.clock.t = T
    ctx.sent = []

    async def notify(method, params):
        ctx.sent.append((method, params))
    ctx.notify = notify
    gate.set_presence(ctx, 90.0, "idle")
    return ctx


M = Moment("Cricinfo reports play starts at 3pm.", "soon", topic="f1", topic_text="India vs NZ", source_name="Cricinfo", key="k1")


@pytest.mark.asyncio
async def test_a_good_moment_tells_him_logs_it_and_puts_it_in_the_chat(g):
    d = await gate.submit(g, M)
    assert d.action == "say"
    t = g.con.execute("SELECT * FROM told").fetchone()
    assert t["said"] == M.text and t["how"] == "say" and t["source_name"] == "Cricinfo" and t["why"]
    assert g.sent == [("proactive.say", {"id": t["id"], "how": "say", "text": M.text, "urgency": "soon", "source_name": "Cricinfo", "url": None})]
    row = g.con.execute("SELECT role, text FROM chat_messages ORDER BY id DESC LIMIT 1").fetchone()
    assert row["role"] == "assistant" and row["text"] == M.text


@pytest.mark.asyncio
async def test_a_quiet_moment_is_shown_as_a_card_never_spoken(g):
    q = Moment("Recording Class.", "now", topic="class", key="kq", quiet=True)
    await gate.submit(g, q, hold=False)
    assert g.con.execute("SELECT how FROM told").fetchone()["how"] == "card"
    assert g.sent[0][1]["how"] == "card"


@pytest.mark.asyncio
async def test_a_bad_moment_is_held_then_told_at_the_next_break(g):
    gate.set_presence(g, 2.0)                                       # typing
    assert (await gate.submit(g, M)).action == "wait"
    assert g.con.execute("SELECT count(*) FROM told").fetchone()[0] == 0
    h = g.con.execute("SELECT * FROM held").fetchone()
    assert h["status"] == "waiting" and h["why"] == "he is working"
    assert await gate.release(g) == 0                               # still typing
    gate.set_presence(g, 40.0)                                      # a break
    assert await gate.release(g) == 1
    assert g.con.execute("SELECT status FROM held").fetchone()[0] == "delivered"
    assert g.con.execute("SELECT waited_s FROM told").fetchone()[0] >= 0


@pytest.mark.asyncio
async def test_class_blocks_and_the_item_waits_until_class_ends(g):
    s = classes.start(g.con, g.clock, "Physics", None, None)
    assert (await gate.submit(g, M)).action == "wait"
    assert await gate.release(g) == 0
    classes.end(g.con, g.clock, s["id"])
    assert await gate.release(g) == 1


@pytest.mark.asyncio
async def test_the_same_news_is_never_told_twice(g):
    await gate.submit(g, M)
    g.clock.t += 600
    assert (await gate.submit(g, M)).action == "drop"
    assert g.con.execute("SELECT count(*) FROM told").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_only_one_is_told_per_release_and_the_most_urgent_goes_first(g):
    s = classes.start(g.con, g.clock, "Physics", None, None)           # in class everything waits
    for n, u in enumerate(("later", "soon", "now"), 1):
        await gate.submit(g, Moment(f"item {n}", u, topic="t", key=f"k{n}"))
    classes.end(g.con, g.clock, s["id"])
    gate.set_presence(g, 300.0)
    assert await gate.release(g) == 1
    assert g.con.execute("SELECT text FROM told").fetchone()[0] == "item 3"
    g.clock.t += gate.MIN_GAP_S + 1
    gate.set_presence(g, 300.0)
    assert await gate.release(g) == 1
    assert [r[0] for r in g.con.execute("SELECT text FROM told ORDER BY at")] == ["item 3", "item 2"]


@pytest.mark.asyncio
async def test_a_held_item_expires_when_it_is_stale(g):
    classes.start(g.con, g.clock, "Physics", None, None)               # held: in class
    await gate.submit(g, Moment("old news", "now", topic="t", key="old"))
    g.clock.t += gate.TTL["now"] + 1
    g.con.execute("UPDATE class_sessions SET status='ended'")
    gate.set_presence(g, 300.0)
    assert await gate.release(g) == 0
    assert g.con.execute("SELECT status FROM held").fetchone()[0] == "expired"
    assert g.con.execute("SELECT count(*) FROM told").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_the_daily_limit_holds_the_rest_until_tomorrow(g):
    g.settings.set("speak_up", {"cap": 2, "quiet_from": 23, "quiet_to": 7})
    for n in range(3):
        await gate.submit(g, Moment(f"item {n}", "soon", topic="t", key=f"k{n}"))
        g.clock.t += gate.MIN_GAP_S + 1
        gate.set_presence(g, 300.0)
    assert g.con.execute("SELECT count(*) FROM told").fetchone()[0] == 2
    assert g.con.execute("SELECT why FROM held WHERE status='waiting'").fetchone()[0] == "daily limit reached"
    g.clock.t = sgt(8, day=10)                                      # next morning
    gate.set_presence(g, 300.0)
    assert await gate.release(g) == 0                               # item 2 is 17 h old: past its soon window (12 h)
    assert g.con.execute("SELECT status FROM held").fetchone()[0] == "expired"


@pytest.mark.asyncio
async def test_two_dismissals_back_off_that_topic_but_not_others(g):
    for n in range(2):
        await gate.submit(g, Moment(f"item {n}", "soon", topic="f1", topic_text="the match", key=f"k{n}"))
        g.clock.t += gate.MIN_GAP_S + 1
        gate.set_presence(g, 300.0)
    g.con.execute("UPDATE told SET feedback='down', feedback_at=at")
    g.con.commit()
    d = await gate.submit(g, Moment("item 3", "now", topic="f1", key="k3"))
    assert d.action == "wait" and d.why == "backing off this topic"
    assert (await gate.submit(g, Moment("other", "now", topic="f2", key="k4"))).action == "say"


@pytest.mark.asyncio
async def test_after_a_dismissal_she_asks_first_and_yes_gives_him_the_text(g):
    await gate.submit(g, M)
    g.con.execute("UPDATE told SET feedback='down', feedback_at=at")
    g.con.commit()
    g.clock.t += 600
    gate.set_presence(g, 300.0)
    d = await gate.submit(g, Moment("Cricinfo reports the match is abandoned.", "soon", topic="f1", topic_text="India vs NZ",
                                    source_name="Cricinfo", key="k2"))
    assert d.action == "ask"
    t = g.con.execute("SELECT * FROM told WHERE how='ask'").fetchone()
    assert t["said"] == "I've got something on India vs NZ. Want to hear it?" and "abandoned" in t["text"]
    assert "abandoned" not in g.sent[-1][1]["text"]                  # the ask does not give the news away
    assert "India vs NZ" in gate.ask_block(g.con, g.clock.now())
    r = gate.answer_ask(g, True)
    assert r["ok"] and "abandoned" in r["text"] and r["source"] == "Cricinfo"
    assert gate.ask_block(g.con, g.clock.now()) == ""
    assert g.con.execute("SELECT feedback FROM told WHERE how='ask'").fetchone()[0] == "accepted"


@pytest.mark.asyncio
async def test_an_ask_that_is_declined_counts_as_a_dismissal_and_nothing_pending_is_an_error(g):
    assert gate.answer_ask(g, True)["ok"] is False
    await gate.submit(g, M)
    g.con.execute("UPDATE told SET feedback='down', feedback_at=at")
    g.con.commit()
    g.clock.t += 600
    gate.set_presence(g, 300.0)
    await gate.submit(g, Moment("more", "soon", topic="f1", topic_text="x", key="k9"))
    assert gate.answer_ask(g, False) == {"ok": True, "accepted": False}
    assert g.con.execute("SELECT feedback FROM told WHERE how='ask'").fetchone()[0] == "declined"


@pytest.mark.asyncio
async def test_an_unanswered_ask_is_forgotten_after_two_hours(g):
    await gate.submit(g, M)
    g.con.execute("UPDATE told SET feedback='down', feedback_at=at")
    g.con.commit()
    g.clock.t += 600
    gate.set_presence(g, 300.0)
    await gate.submit(g, Moment("more", "soon", topic="f1", topic_text="x", key="k9"))
    g.clock.t += gate.ASK_WINDOW_S + 1
    assert gate.ask_block(g.con, g.clock.now()) == ""


def test_options_change_the_limit_and_quiet_hours_and_refuse_nonsense(g):
    assert gate.set_option(g, "daily_limit", 3)["daily_limit"] == 3
    r = gate.set_option(g, "quiet_from", 22)
    assert r["quiet_from"] == 22 and r["quiet_to"] == 7 and r["daily_limit"] == 3
    for bad in (("daily_limit", 99), ("daily_limit", "lots"), ("quiet_to", 24), ("mood", 1)):
        assert gate.set_option(g, *bad)["ok"] is False
    assert g.settings.get("speak_up") == {"cap": 3, "quiet_from": 22, "quiet_to": 7}


# ---- from a follow, through the gate

@pytest.mark.asyncio
async def test_a_new_finding_from_a_follow_goes_through_the_gate_but_the_first_look_does_not(g, monkeypatch):
    g.net.online = True
    g.settings.set("paid_calls_allowed", True)
    fake_web(monkeypatch, [("https://cricinfo.test/1", "Rain", "Toss delayed")],
             [("https://cricinfo.test/2", "Play starts", "Play to start at 3pm"), ("https://cricinfo.test/1", "Rain", "x")])
    g.openrouter = ScriptBrain(judged(True, "Cricinfo reports the toss was delayed."),
                               {"content": '{"worth": true, "say": "Cricinfo reports play starts at 3pm.", "items": [1], "when": "now"}'})
    f = make(g)
    await follows.check(g, f)
    assert g.con.execute("SELECT count(*) FROM told").fetchone()[0] == 0 and g.sent == []     # told in chat already
    f = g.con.execute("SELECT * FROM follows WHERE id=?", (f["id"],)).fetchone()
    await follows.check(g, f)
    t = g.con.execute("SELECT * FROM told").fetchone()
    assert t["urgency"] == "now" and t["source_name"] == "cricinfo.test" and "India vs NZ" in t["topic_text"]


@pytest.mark.asyncio
async def test_while_he_types_the_finding_is_held_and_a_missing_urgency_means_soon(g, monkeypatch):
    g.net.online = True
    g.settings.set("paid_calls_allowed", True)
    gate.set_presence(g, 1.0)
    fake_web(monkeypatch, [("https://a.test/1", "x", "x")], [("https://a.test/2", "y", "y"), ("https://a.test/1", "x", "x")])
    g.openrouter = ScriptBrain(judged(False, "", ()), judged(True, "A.test reports y."))
    f = make(g)
    await follows.check(g, f)
    f = g.con.execute("SELECT * FROM follows WHERE id=?", (f["id"],)).fetchone()
    await follows.check(g, f)
    h = g.con.execute("SELECT * FROM held").fetchone()
    assert h["urgency"] == "soon" and h["status"] == "waiting"


# ---- chat

@pytest.mark.asyncio
async def test_she_can_change_the_limit_by_chat_and_the_rule_no_longer_forbids_telling_him(g):
    g.ctx = g
    g.openrouter = ScriptBrain({"calls": [call("speak_up", setting="daily_limit", value=2)]},
                               {"content": "Okay, two a day."})
    await say(g, "you're telling me too much")
    assert g.settings.get("speak_up")["cap"] == 2
    fixed, _ = chat.system_parts(g, "x")
    assert "never promise to alert him" not in fixed and "gate" in fixed


@pytest.mark.asyncio
async def test_a_pending_ask_is_in_the_prompt_and_the_exam_prompt_is_untouched(g):
    await gate.submit(g, M)
    g.con.execute("UPDATE told SET feedback='down', feedback_at=at")
    g.con.commit()
    g.clock.t += 600
    gate.set_presence(g, 300.0)
    await gate.submit(g, Moment("more", "soon", topic="f1", topic_text="India vs NZ", key="k9"))
    _, changing = chat.system_parts(g, "sure")
    assert "answer_ask" in changing and "India vs NZ" in changing
    fixed_e, changing_e = chat.system_parts(g, "sure", exam=True)
    assert "answer_ask" not in fixed_e + changing_e and "speak_up" not in fixed_e


@pytest.mark.asyncio
async def test_chat_yes_runs_answer_ask(g):
    await gate.submit(g, M)
    g.con.execute("UPDATE told SET feedback='down', feedback_at=at")
    g.con.commit()
    g.clock.t += 600
    gate.set_presence(g, 300.0)
    await gate.submit(g, Moment("Cricinfo reports a super over.", "soon", topic="f1", topic_text="India vs NZ", key="k9"))
    g.openrouter = ScriptBrain({"calls": [call("answer_ask", accepted=True)]}, {"content": "Cricinfo reports a super over."})
    await say(g, "sure go on")
    tool_msg = [m for m in g.openrouter.bodies[1]["messages"] if m["role"] == "tool"][0]
    assert "super over" in tool_msg["content"]


# ---- the app's side

@pytest.mark.asyncio
async def test_the_presence_and_told_list_rpcs(g, client=None):
    from misty_engine.api import build_methods
    m = build_methods(g)
    assert (await m["presence.set"]({"idle_s": 33, "voice": "speaking"})) == {"ok": True}
    p = gate.presence(g)
    assert p.idle_s == 33 and p.voice == "speaking" and p.at == g.clock.now()
    await m["presence.set"]({"idle_s": None})
    assert gate.presence(g).idle_s is None
    await m["presence.set"]({"idle_s": 99})
    await gate.submit(g, M)
    await gate.submit(g, Moment("later one", "later", topic="f2", key="z"))
    r = await m["told.list"]({})
    assert r["told"][0]["said"] == M.text and r["daily_limit"] == 5 and r["told_today"] == 1
