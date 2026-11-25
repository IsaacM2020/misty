"""The morning brief (Season 3, Day 8): facts from what she already has, one phrasing call that adds nothing, written the
first time he is at the Mac in the morning, told through the gate, and "let's go" opens only what she offered."""
import asyncio
import json

import pytest

from misty_engine import brief, chat, classes, gate
from test_chat import ScriptBrain, call, say
from test_gate import g, sgt  # noqa: F401


def fake_world(monkeypatch, tmp_path):
    folder = tmp_path / "ib"
    folder.mkdir()

    async def cal(ctx, args, on_status):
        return {"ok": True, "events": ["08:00-09:00 Physics class", "15:00 Chess club"],
                "links": [{"title": "Physics class", "url": "https://meet.google.com/abc-defg"}]}

    monkeypatch.setattr(chat, "_calendar", cal)
    monkeypatch.setattr(chat, "_mail", lambda ctx, a: {"ok": True, "mail": [
        {"subject": "IA moved", "from": "Ms Smitha", "text": "The IA is now due Friday.", "isaac_replied": False},
        {"subject": "Receipt", "from": "Shop", "text": "Thanks", "isaac_replied": True}]})
    monkeypatch.setattr(chat, "_recent_files", lambda ctx, a: {"ok": True, "busiest": [{"folder": str(folder), "files": 4}]})
    return folder


JSON_OK = {"content": json.dumps({"say": "Good morning. Your mail from Ms Smitha says the IA is now due Friday. Physics is at eight. "
                                         "Work on the IA today. Say let's go and I'll open it.", "task": "the IA", "open": ["f2"]})}


@pytest.fixture
def B(g, monkeypatch, tmp_path):
    g.settings.set("paid_calls_allowed", True)
    g.net.online = True
    g.settings.set("folders", [str(tmp_path)])
    g.folder = fake_world(monkeypatch, tmp_path)
    g.clock.t = sgt(7, 30)
    gate.set_presence(g, 5.0)                                  # at the keyboard
    return g


@pytest.mark.asyncio
async def test_the_facts_come_from_what_she_has_and_only_that(B):
    B.con.execute("INSERT INTO cares(id,key,kind,text,weight,status,created_at,updated_at) VALUES ('c','ib math','goal','a 7 in IB Math HL',3,'active',0,0)")
    s = classes.start(B.con, B.clock, "Physics", None, None)
    B.con.execute("INSERT INTO class_actions(id, session_id, text, kind, due) VALUES ('ca_1', ?, 'Finish the IA draft', 'homework', '2026-11-10')", (s["id"],))
    B.con.execute("INSERT INTO class_actions(id, session_id, text, kind, due) VALUES ('ca_2', ?, 'Done thing', 'homework', '2026-11-10')", (s["id"],))
    B.con.execute("UPDATE class_actions SET done=1 WHERE id='ca_2'")
    B.con.execute("INSERT INTO follows(id,text,source,query,every_min,until_at,created_at,next_at) VALUES ('f1','India vs NZ','web','q',30,9e12,0,0)")
    B.con.execute("INSERT INTO follow_findings(id,follow_id,at,kind,text,source_name) VALUES ('x1','f1',?,'new','Cricinfo reports rain.','Cricinfo')", (B.clock.now() - 3600,))
    B.con.commit()
    f = (await brief.gather(B))["facts"]
    assert f["calendar_today"][0].startswith("08:00") and f["new_mail"][0]["from"] == "Ms Smitha" and len(f["new_mail"]) == 1   # replied-to mail left out
    assert f["watching_found"][0]["said"] == "Cricinfo reports rain." and f["goals"] == ["ib math: a 7 in IB Math HL"]
    assert [a["what"] for a in f["due_soon"]] == ["Finish the IA draft"] and f["due_soon"][0]["subject"] == "Physics"
    assert {o["id"] for o in f["openable"]} == {"u1", "f2"}


@pytest.mark.asyncio
async def test_compose_keeps_only_openable_ids_she_was_given_and_refuses_an_empty_brief(B):
    g_ = await brief.gather(B)
    B.openrouter = ScriptBrain({"content": json.dumps({"say": "Good morning.", "task": "x", "open": ["f2", "f99", "rm -rf"]})},
                               {"content": json.dumps({"say": "", "task": "", "open": []})})
    out = await brief.compose(B, g_)
    assert [o["id"] for o in out["opens"]] == ["f2"]
    with pytest.raises(ValueError):
        await brief.compose(B, g_)


@pytest.mark.asyncio
async def test_the_brief_is_written_once_when_he_is_first_at_the_mac_and_told_at_a_break(B):
    B.openrouter = ScriptBrain(JSON_OK)
    gate.set_presence(B, 30.0)                                 # a pause, still around
    r = await brief.ensure_today(B)
    assert r["task"] == "the IA" and [o["id"] for o in r["opens"]] == ["f2"]
    row = brief.today_row(B.con, B.clock.now())
    assert row["say"].startswith("Good morning.") and json.loads(row["facts"])["new_mail"]
    t = B.con.execute("SELECT * FROM told").fetchone()
    assert t["said"] == row["say"] and t["topic"] == "brief" and t["how"] == "say"
    assert await brief.ensure_today(B) is None                 # once a day
    assert len(B.openrouter.bodies) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("why,hour,idle,session", [("too early", 4, 5.0, False), ("morning is over", 12, 5.0, False),
                                                   ("not at the Mac (long idle)", 7, 4000.0, False), ("in class", 7, 5.0, True)])
async def test_no_brief_when_it_is_not_the_moment(B, why, hour, idle, session):
    B.clock.t = sgt(hour, 30)
    gate.set_presence(B, idle)
    if session:
        classes.start(B.con, B.clock, "Physics", None, None)
    B.openrouter = ScriptBrain(JSON_OK)
    assert await brief.ensure_today(B) is None, why
    assert not B.openrouter.bodies


@pytest.mark.asyncio
async def test_no_budget_or_offline_means_no_brief_and_no_crash(B):
    B.settings.set("paid_calls_allowed", False)
    assert await brief.ensure_today(B) is None
    B.settings.set("paid_calls_allowed", True)
    B.net.online = False
    assert await brief.ensure_today(B) is None


@pytest.mark.asyncio
async def test_a_failing_model_retries_then_stops_after_three_tries(B):
    B.openrouter = ScriptBrain({"content": "not json"}, {"content": "still not"}, {"content": "nope"}, JSON_OK)
    for _ in range(4):
        await brief.ensure_today(B)
    assert brief.today_row(B.con, B.clock.now()) is None and len(B.openrouter.bodies) == 3


@pytest.mark.asyncio
async def test_lessons_about_news_never_hold_back_the_brief(B):
    B.con.execute("INSERT INTO lessons(id,text,created_at,updated_at) VALUES ('l','Don''t tell him about mail',0,0)")
    B.con.commit()
    B.openrouter = ScriptBrain(JSON_OK)
    gate.set_presence(B, 30.0)
    await brief.ensure_today(B)
    assert B.con.execute("SELECT count(*) FROM told").fetchone()[0] == 1 and len(B.openrouter.bodies) == 1     # no verdict call


@pytest.mark.asyncio
async def test_lets_go_opens_only_what_she_offered_and_only_inside_his_folders(B, monkeypatch, tmp_path):
    opened = []

    async def fake_open(t):
        opened.append(t)
    monkeypatch.setattr(chat, "_open", fake_open)
    B.openrouter = ScriptBrain(JSON_OK)
    await brief.ensure_today(B)
    # by default: today's brief task
    B.openrouter = ScriptBrain({"calls": [call("lets_go")]}, {"content": "Opened your folder."})
    out, _, _ = await say(B, "let's go")
    assert opened == [str(B.folder)] and out["actions"][0]["ok"]
    # an id she never offered, or a path outside his folders, is refused
    items = [{"id": "f9", "kind": "folder", "target": "/etc", "label": "etc"}, {"id": "u9", "kind": "link", "target": "http://x.test", "label": "x"},
             {"id": "u8", "kind": "link", "target": "https://ok.test/a", "label": "ok"}]
    assert [brief.safe_target(o, [str(tmp_path)]) for o in items] == [False, False, True]
    assert brief.lets_go_items(B, ["nope"]) == []


@pytest.mark.asyncio
async def test_what_should_i_work_on_returns_the_facts_and_remembers_what_can_be_opened(B, monkeypatch):
    opened = []

    async def fake_open(t):
        opened.append(t)
    monkeypatch.setattr(chat, "_open", fake_open)
    B.openrouter = ScriptBrain({"calls": [call("work_on")]}, {"content": "Work on the IA. Say let's go."},
                               {"calls": [call("lets_go", ids=["f2"])]}, {"content": "Opened it."})
    await say(B, "what should I work on")
    tool = [m for m in B.openrouter.bodies[1]["messages"] if m["role"] == "tool"][0]["content"]
    assert "Ms Smitha" in tool and "f2" in tool
    await say(B, "let's go")
    assert opened == [str(B.folder)]


@pytest.mark.asyncio
async def test_the_exam_prompt_has_no_brief_rule_and_the_rule_is_in_the_normal_one(B):
    fixed, _ = chat.system_parts(B, "x")
    assert "work_on" in fixed and "lets_go" in fixed
    fixed_e, _ = chat.system_parts(B, "x", exam=True)
    assert "work_on" not in fixed_e


@pytest.mark.asyncio
async def test_with_nothing_to_open_she_does_not_offer_to_open_it(B):
    g_ = await brief.gather(B)
    B.openrouter = ScriptBrain({"content": json.dumps({"say": "Good morning. Work on the clickers. Say let's go and I'll open it.",
                                                       "task": "clickers", "open": []})})
    out = await brief.compose(B, g_)
    assert out["opens"] == [] and "let's go" not in out["say"] and out["say"].startswith("Good morning.")


@pytest.mark.asyncio
async def test_she_is_told_the_time_in_the_part_that_is_not_cached_and_never_in_the_exam(B):
    fixed, changing = chat.system_parts(B, "x")
    assert "Right now it is " in changing and "Right now it is " not in fixed
    assert "Right now it is " not in "".join(chat.system_parts(B, "x", exam=True))


@pytest.mark.asyncio
async def test_the_brief_reads_google_and_spotlight_at_the_same_time(B, monkeypatch):
    import time
    from misty_engine import chat as _chat

    async def slow_cal(ctx, args, st):
        await asyncio.sleep(0.3)
        return {"ok": True, "events": ["09:00-10:00 Maths"], "links": []}

    def slow_files(ctx, args):
        time.sleep(0.3)
        return {"ok": True, "busiest": []}
    monkeypatch.setattr(_chat, "_calendar", slow_cal)
    monkeypatch.setattr(_chat, "_recent_files", slow_files)
    t0 = time.monotonic()
    g = await brief.gather(B)
    assert time.monotonic() - t0 < 0.5 and g["facts"]["calendar_today"] == ["09:00-10:00 Maths"]


def test_a_forced_brief_turn_skips_the_notes_search_and_the_exam_is_untouched(B, monkeypatch):
    from misty_engine import chat as _chat
    seen = []
    monkeypatch.setattr(_chat, "_notes", lambda ctx, q, b: seen.append(q) or "NOTES")
    _, _, changing = _chat.system_blocks(B, "what's up", voice=True, notes_on=False)
    assert seen == [] and "NOTES" not in changing
    _, _, changing = _chat.system_blocks(B, "what's up", voice=True)
    assert seen == ["what's up"] and "NOTES" in changing
