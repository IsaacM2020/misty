"""Follows (Season 3, Day 4): a follow is a sentence + a plan; checks run on their own, free until something is new,
then one small judge call; findings name their source; bad sources back off; budgets and the end date hold."""
import json

import pytest

from misty_engine import chat, follows, web
from misty_engine.errors import Fatal
from test_chat import ScriptBrain, call, say, sys_text

RSS = """<?xml version="1.0"?><rss><channel><item><title>India bow first in rainy Wellington</title>
<link>https://x.test/a</link><guid>a</guid><description>&lt;p&gt;Rain delayed the toss.&lt;/p&gt;</description></item>
<item><title>Series preview</title><link>https://x.test/b</link><guid>b</guid><description>Preview.</description></item></channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>New model out</title><id>urn:1</id>
<link href="https://y.test/1"/><summary>A lab shipped a model.</summary><updated>2026-11-08</updated></entry></feed>"""


def judged(worth, say_, items=(1,)):
    return {"content": json.dumps({"worth": worth, "say": say_, "items": list(items)})}


@pytest.fixture
def live(ctx):
    ctx.net.online = True
    ctx.settings.set("paid_calls_allowed", True)
    return ctx


def make(ctx, **kw):
    a = dict(text="India vs NZ: delays and key moments", source="web", query="india nz odi live", every_min=15, hours=48)
    a.update(kw)
    r = follows.add(ctx, a["text"], a["source"], a["query"], a.get("url"), a["every_min"], a["hours"])
    return ctx.con.execute("SELECT * FROM follows WHERE id=?", (r["id"],)).fetchone()


def fake_web(monkeypatch, *batches):
    """Each search returns the next batch of (url, title, snippet)."""
    q = list(batches)

    async def search(query, k=5):
        b = q.pop(0) if len(q) > 1 else q[0]
        return [{"url": u, "title": t, "snippet": s} for u, t, s in b]
    monkeypatch.setattr(web, "search", search)


def test_add_clamps_how_often_and_how_long(ctx):
    r = follows.add(ctx, "AI model news", "web", "new ai model", None, 1, 99999)
    f = ctx.con.execute("SELECT * FROM follows WHERE id=?", (r["id"],)).fetchone()
    assert f["every_min"] == 15 and f["until_at"] - ctx.clock.now() == follows.MAX_HOURS * 3600
    r = follows.add(ctx, "school mail", "mail", "ia deadline", None, 1, 24)
    assert ctx.con.execute("SELECT every_min FROM follows WHERE id=?", (r["id"],)).fetchone()[0] == 5


def test_add_refuses_what_cannot_work(ctx):
    for args, code in [(("x", "web", ""), "NEEDS_QUERY"), (("x", "rss", "q"), "BAD_SOURCE"), (("", "web", "q"), "BAD_FOLLOW"),
                       (("x", "feed", "", "ftp://x"), "BAD_FEED_URL")]:
        with pytest.raises(Fatal) as e:
            follows.add(ctx, *args)
        assert str(e.value) == code
    with pytest.raises(Fatal):
        follows.add(ctx, "wifi sk-abcdefghijklmnopqrstuvwxyz0123456789", "web", "q")


def test_at_most_ten_active_follows(ctx):
    for i in range(follows.MAX_ACTIVE):
        follows.add(ctx, f"thing {i}", "web", f"q{i}")
    with pytest.raises(Fatal) as e:
        follows.add(ctx, "one more", "web", "q")
    assert str(e.value) == "TOO_MANY_FOLLOWS"


def test_parse_feed_reads_rss_and_atom_and_refuses_entities():
    r = follows.parse_feed(RSS)
    assert [i["id"] for i in r] == ["a", "b"] and r[0]["text"] == "Rain delayed the toss."
    a = follows.parse_feed(ATOM)
    assert a[0]["id"] == "urn:1" and a[0]["url"] == "https://y.test/1"
    with pytest.raises(ValueError):
        follows.parse_feed('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel/></rss>')
    with pytest.raises(ValueError):
        follows.parse_feed("<html>not a feed")


@pytest.mark.asyncio
async def test_first_look_then_quiet_then_a_new_item_is_a_finding_that_names_its_source(live, monkeypatch):
    ctx = live
    fake_web(monkeypatch, [("https://cricinfo.test/1", "Rain at Wellington", "Toss delayed by rain")],
             [("https://cricinfo.test/1", "Rain at Wellington", "Toss delayed by rain")],
             [("https://cricinfo.test/2", "Play starts", "Play to start at 3pm"), ("https://cricinfo.test/1", "Rain", "x")])
    ctx.openrouter = ScriptBrain(judged(True, "Cricinfo reports the toss was delayed by rain."),
                                 judged(True, "Cricinfo reports play starts at 3pm."))
    f = make(ctx)
    r1 = await follows.check(ctx, f)                                    # first look
    assert r1["finding"]["kind"] == "baseline" and "delayed by rain" in r1["finding"]["text"]
    assert r1["finding"]["source_name"] == "cricinfo.test"
    f = ctx.con.execute("SELECT * FROM follows WHERE id=?", (f["id"],)).fetchone()
    r2 = await follows.check(ctx, f)                                    # nothing new: no model call, no finding
    assert r2["new"] == 0 and r2.get("finding") is None and len(ctx.openrouter.bodies) == 1
    f = ctx.con.execute("SELECT * FROM follows WHERE id=?", (f["id"],)).fetchone()
    r3 = await follows.check(ctx, f)
    assert r3["finding"]["kind"] == "new" and r3["finding"]["url"] == "https://cricinfo.test/2"
    assert ctx.con.execute("SELECT count(*) FROM follow_findings").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_not_worth_telling_stores_nothing_but_marks_items_seen(live, monkeypatch):
    ctx = live
    fake_web(monkeypatch, [("https://a.test/1", "t", "s")])
    ctx.openrouter = ScriptBrain(judged(False, "", ()))
    f = make(ctx)
    r = await follows.check(ctx, f)
    assert r.get("finding") is None
    assert json.loads(ctx.con.execute("SELECT seen FROM follows").fetchone()[0]) == ["https://a.test/1"]


@pytest.mark.asyncio
async def test_a_garbled_judge_answer_is_no_finding_not_a_crash(live, monkeypatch):
    ctx = live
    fake_web(monkeypatch, [("https://a.test/1", "t", "s")])
    ctx.openrouter = ScriptBrain({"content": "sure, here you go!"})
    r = await follows.check(ctx, make(ctx))
    assert r["ok"] and r.get("finding") is None


@pytest.mark.asyncio
async def test_a_failing_source_backs_off_and_doubles(live, monkeypatch):
    ctx = live

    async def boom(q, k=5):
        raise ValueError("HTTPStatusError")
    monkeypatch.setattr(web, "search", boom)
    f = make(ctx)
    t0 = ctx.clock.now()
    r = await follows.check(ctx, f)
    assert r["ok"] is False
    f = ctx.con.execute("SELECT * FROM follows").fetchone()
    assert f["errors"] == 1 and f["last_error"] == "HTTPStatusError" and f["next_at"] - t0 == 15 * 60 * 2
    await follows.check(ctx, f)
    assert ctx.con.execute("SELECT next_at, errors FROM follows").fetchone()[1] == 2


@pytest.mark.asyncio
async def test_no_budget_holds_the_items_instead_of_losing_them(live, monkeypatch):
    ctx = live
    fake_web(monkeypatch, [("https://a.test/1", "t", "s")])
    ctx.openrouter = ScriptBrain(judged(True, "A says x."))
    monkeypatch.setattr(follows, "FOLLOW_USD_PER_DAY", 0.0)
    f = make(ctx)
    r = await follows.check(ctx, f)
    assert "no budget" in r["held"] and ctx.openrouter.bodies == []
    assert json.loads(ctx.con.execute("SELECT seen FROM follows").fetchone()[0]) == []
    monkeypatch.setattr(follows, "FOLLOW_USD_PER_DAY", 0.10)
    r = await follows.check(ctx, ctx.con.execute("SELECT * FROM follows").fetchone())
    assert r["finding"]["text"] == "A says x."


@pytest.mark.asyncio
async def test_feed_and_mail_follows_use_the_same_four_steps(live, monkeypatch):
    ctx = live

    async def get(url):
        return RSS
    monkeypatch.setattr(web, "_get", get)
    monkeypatch.setattr(web, "_public", lambda u: True)
    monkeypatch.setattr("misty_engine.chat._mail", lambda c, a: {"mail": [
        {"subject": "IA deadline moved", "date": "2026-11-08", "from": "Ms Smitha", "text": "The IA is now due Friday."}]})
    ctx.openrouter = ScriptBrain(judged(True, "x.test reports rain delayed the toss."),
                                 judged(True, "Your mail from Ms Smitha says the IA is now due Friday."))
    feed = make(ctx, source="feed", query="", url="https://x.test/rss.xml", every_min=10)
    mail = make(ctx, text="school mail about the IA", source="mail", query="IA deadline", every_min=5)
    assert (await follows.check(ctx, feed))["finding"]["source_name"] == "x.test"
    m = await follows.check(ctx, mail)
    assert m["finding"]["source_name"] == "your mail from Ms Smitha"


@pytest.mark.asyncio
async def test_run_due_checks_only_due_ones_ends_the_expired_and_waits_offline(live, monkeypatch):
    ctx = live
    fake_web(monkeypatch, [("https://a.test/1", "t", "s")])
    ctx.openrouter = ScriptBrain(judged(False, "", ()), judged(False, "", ()))
    a, b = make(ctx), make(ctx, text="other", query="other q", hours=1)
    assert await follows.run_due(ctx) == 0                              # nothing due yet
    ctx.clock.advance(16 * 60)
    assert await follows.run_due(ctx) == 2
    ctx.net.online = False
    ctx.clock.advance(7200)
    assert await follows.run_due(ctx) == 0
    ctx.net.online = True
    assert await follows.run_due(ctx) >= 0
    assert ctx.con.execute("SELECT status FROM follows WHERE id=?", (b["id"],)).fetchone()[0] == "ended"


def test_stop_by_id_or_words_and_the_prompt_block_shows_the_latest_finding(ctx):
    f = make(ctx)
    ctx.con.execute("INSERT INTO follow_findings VALUES ('ff_1', ?, ?, 'new', 'Cricinfo reports play at 3pm.', 'cricinfo.test', NULL)",
                    (f["id"], ctx.clock.now()))
    b = follows.block(ctx.con)
    assert f["id"] in b and "Cricinfo reports play at 3pm." in b and "every 15 min" in b
    assert follows.stop(ctx, "india vs nz")["stopped"] == 1 and follows.active(ctx.con) == []
    assert follows.stop(ctx, "nothing")["stopped"] == 0


@pytest.mark.asyncio
async def test_chat_follow_tool_reports_the_first_look_and_a_bad_source_is_dropped(live, monkeypatch):
    ctx = live
    fake_web(monkeypatch, [("https://a.test/1", "Rain", "Toss delayed")])
    ctx.openrouter = ScriptBrain(
        {"calls": [call("follow", text="India vs NZ delays", source="web", query="india nz live", every_minutes=15, hours=24)]},
        judged(True, "A.test reports the toss was delayed."),     # the judge call (first look)
        {"content": "Watching it, A.test says the toss was delayed."})
    out, text, _ = await say(ctx, "keep an eye on the india nz match")
    assert out["actions"][0]["tool"] == "follow" and out["actions"][0]["ok"]
    assert len(follows.active(ctx.con)) == 1

    async def boom(q, k=5):
        raise ValueError("HTTPStatusError")
    monkeypatch.setattr(web, "search", boom)
    ctx.openrouter = ScriptBrain({"calls": [call("follow", text="x", source="web", query="q")]}, {"content": "That source failed."})
    out, _, _ = await say(ctx, "watch something else")
    assert out["actions"][0]["ok"] is False and len(follows.active(ctx.con)) == 1   # the failed one was dropped


@pytest.mark.asyncio
async def test_chat_unfollow_and_the_exam_prompt_has_no_follow_text(ctx):
    f = make(ctx)
    ctx.openrouter = ScriptBrain({"calls": [call("unfollow", key=f["id"])]}, {"content": "Stopped."})
    out, _, _ = await say(ctx, "stop watching that")
    assert out["actions"][0]["ok"] and follows.active(ctx.con) == []
    follows.add(ctx, "AI news", "web", "ai news")
    fixed, _ = chat.system_parts(ctx, "hi", exam=True)
    assert "keeping an eye on" not in fixed and "call follow" not in fixed
    fixed, _ = chat.system_parts(ctx, "hi")
    assert "call follow" in fixed and "AI news" in fixed


@pytest.mark.asyncio
async def test_follows_list_rpc_is_what_the_radar_shows(live, monkeypatch):
    from misty_engine.api import build_methods
    ctx = live
    fake_web(monkeypatch, [("https://a.test/1", "Rain", "Toss delayed")])
    ctx.openrouter = ScriptBrain(judged(True, "A.test reports the toss was delayed."))
    f = make(ctx)
    await follows.check(ctx, f)
    out = (await build_methods(ctx)["follows.list"]({}))["follows"]
    assert len(out) == 1 and out[0]["latest"] == "A.test reports the toss was delayed." and out[0]["latest_source"] == "a.test"
    assert out[0]["every_min"] == 15 and out[0]["checks"] == 1


def test_an_ask_about_a_follow_reads_as_speech_not_as_his_instruction():
    from misty_engine import gate
    t = follows.ask_topic("Tell Isaac when a new AI model comes out from a major lab.")
    assert t == "what you asked me to watch for: a new AI model comes out from a major lab"
    said = gate._ask_text(gate.Moment("x", "soon", topic="f1", topic_text=t))
    assert said == "I've got something on what you asked me to watch for: a new AI model comes out from a major lab. Want to hear it?"
    assert ".." not in said
    assert follows.ask_topic("India vs NZ final.") == "what you asked me to watch for: India vs NZ final"
    assert follows.ask_topic("") == "something you asked me to watch"
    assert ".." not in gate._ask_text(gate.Moment("x", "soon", topic="f1", topic_text="the match."))


@pytest.mark.asyncio
async def test_the_judge_is_shown_what_he_was_already_told_so_the_same_fact_is_not_told_twice(live, monkeypatch):
    ctx = live
    fake_web(monkeypatch, [("https://a.test/1", "City win", "City beat Inter 4-1")],
             [("https://a.test/1", "City win", "City beat Inter 4-1"), ("https://b.test/2", "Report", "Inter lost 1-4 to City")])
    ctx.openrouter = ScriptBrain(judged(True, "A.test reports City beat Inter 4-1."), judged(False, "", ()))
    f = make(ctx)
    await follows.check(ctx, f)
    f = ctx.con.execute("SELECT * FROM follows WHERE id=?", (f["id"],)).fetchone()
    r = await follows.check(ctx, f)
    assert r.get("finding") is None
    first, second = (b["messages"] for b in ctx.openrouter.bodies)
    assert "Already told him" not in first[1]["content"]                       # the first look has nothing to repeat
    assert "Already told him:\n- A.test reports City beat Inter 4-1." in second[1]["content"]
    assert "the same fact from another source is not new" in second[0]["content"]
