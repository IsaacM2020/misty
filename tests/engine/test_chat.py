import asyncio
"""Chat + facts: remember replaces by key, 'this' attaches what's open, open_fact is the fast path, no fact means no
guess, the tool rounds are capped, and the reply streams."""
import json

import httpx
import pytest

from misty_engine import chat, facts, openrouter
from misty_engine.errors import Fatal


def sys_text(m):
    """The system message's text: it goes out as cached + changing blocks."""
    c = m["content"]
    return c if isinstance(c, str) else "\n\n".join(b["text"] for b in c)


def call(name, **args):
    return {"id": "t_" + name, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptBrain:
    """Stands in for ctx.openrouter.post: plays back one reply per round and streams its text."""
    def __init__(self, *replies):
        self.replies, self.bodies, self.approved = list(replies), [], []

    async def post(self, path, body, *, on_delta=None, **kw):
        self.bodies.append(json.loads(json.dumps(body)))
        self.approved.append(openrouter._USER_APPROVED.get())
        r = self.replies.pop(0) if self.replies else {"content": "ok"}
        if on_delta and r.get("content"):
            for w in r["content"].split(" "):
                await on_delta(w + " ")
        msg = {"role": "assistant", "content": r.get("content", "")}
        if r.get("calls"):
            msg["tool_calls"] = r["calls"]
        return {"choices": [{"message": msg}], "usage": {"cost": 0.0004}}


async def nothing(_):
    return None


async def no(_):
    return False


async def say(ctx, text, confirm=no):
    out, pieces, statuses = None, [], []

    async def d(p):
        pieces.append(p)

    async def s(t):
        statuses.append(t)
    out = await chat.send(ctx, text, on_delta=d, on_status=s, confirm=confirm)
    return out, "".join(pieces), statuses


@pytest.fixture
def opened(monkeypatch):
    got = []

    async def op(target):
        got.append(target)
    monkeypatch.setattr(chat, "_open", op)
    return got


@pytest.mark.asyncio
async def test_this_doc_is_saved_with_its_link_then_opened_by_the_fast_path(ctx, opened, monkeypatch):
    async def front(_ctx):
        return {"link": "https://docs.google.com/document/d/abc", "title": "Macbeth essay", "app": "Safari"}
    monkeypatch.setattr(facts, "front_thing", front)
    ctx.openrouter = ScriptBrain(
        {"calls": [call("remember", key="English task", text="Isaac's English task is this doc", attach_front=True)]},
        {"content": "Saved, your English task is the Macbeth essay."})
    out, streamed, _ = await say(ctx, "my english task is this doc")
    assert out["actions"][0] == {"tool": "remember", "ok": True, "key": "english task", "action": "added"}
    assert "Macbeth essay" in streamed and ctx.openrouter.approved == [True, True]
    row = facts.get(ctx.con, "english task")
    assert row["link"].endswith("/abc") and row["link_title"] == "Macbeth essay"

    ctx.openrouter = ScriptBrain({"calls": [call("open_fact", key="english task")]},
                                 {"content": "Opened your Macbeth essay."})
    out, _, statuses = await say(ctx, "do my english work")
    assert opened == ["https://docs.google.com/document/d/abc"]
    assert out["actions"][0]["opened"] == "Macbeth essay" and statuses == ["Opening Macbeth essay…"]
    system = sys_text(ctx.openrouter.bodies[0]["messages"][0])
    assert "- english task: Isaac's English task is \u201CMacbeth essay\u201D [link: Macbeth essay]" in system
    hist = ctx.openrouter.bodies[0]["messages"]
    # the earlier turn comes back with its real tool call, so the model never learns to just say "Saved"
    assert [m["role"] for m in hist[1:]] == ["user", "assistant", "tool", "assistant", "user"]
    assert hist[2]["tool_calls"][0]["function"]["name"] == "remember" and '"ok": true' in hist[3]["content"]


@pytest.mark.asyncio
async def test_same_key_replaces_and_keeps_history(ctx):
    facts.remember(ctx, "English task", "the Macbeth essay")
    r = facts.remember(ctx, "english  task", "the poetry commentary")
    assert r["action"] == "updated" and r["was"] == "the Macbeth essay"
    assert [x["text"] for x in facts.active(ctx.con)] == ["the poetry commentary"]
    assert ctx.con.execute("select count(*) from facts where status='replaced'").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_near_duplicate_key_merges_into_the_existing_one(ctx):
    facts.remember(ctx, "physics tutor", "Mr Tan, Tuesdays")
    r = facts.remember(ctx, "physics tutors", "Mr Tan, Thursdays now")
    assert r == {**r, "action": "updated", "key": "physics tutor"}
    assert len(facts.active(ctx.con)) == 1
    assert facts.remember(ctx, "chem tutor", "Ms Lee")["action"] == "added"


def test_secrets_are_never_saved(ctx):
    with pytest.raises(Fatal, match="LOOKS_SECRET"):
        facts.remember(ctx, "openrouter key", "sk-or-v1-" + "a1b2c3d4" * 8)
    assert facts.active(ctx.con) == []


@pytest.mark.asyncio
async def test_no_fact_means_no_guess(ctx, opened):
    ctx.openrouter = ScriptBrain({"calls": [call("open_fact", key="chemistry work")]},
                                 {"content": "I don't have your chemistry work yet."})
    out, _, _ = await say(ctx, "open my chemistry work")
    tool_msg = ctx.openrouter.bodies[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "no saved fact" in tool_msg["content"]
    assert opened == [] and out["actions"][0]["ok"] is False


@pytest.mark.asyncio
async def test_tool_rounds_are_capped_then_it_must_answer(ctx):
    loop_call = {"calls": [call("recall", query="x")]}
    ctx.openrouter = ScriptBrain(*([loop_call] * 6), {"content": "Here's what I found."})
    out, _, _ = await say(ctx, "what's up")
    bodies = ctx.openrouter.bodies
    assert len(bodies) == chat.MAX_ROUNDS + 1
    assert bodies[-1]["tool_choice"] == "none" and all(b["tool_choice"] == "auto" for b in bodies[:-1])


@pytest.mark.asyncio
async def test_risky_steps_inside_do_ask_in_the_chat(ctx, monkeypatch):
    class Hands:
        connected = True
    ctx.extras = {"hands": Hands()}
    asked = []

    async def fake_run(task, *, confirm, context, **kw):
        assert "english task" in context                 # memory drives hands
        ok = await confirm("Send the email to Mr Tan?")
        from types import SimpleNamespace
        return SimpleNamespace(outcome="done" if ok else "blocked", summary="sent" if ok else "stopped before sending")
    from misty_engine.stream import loop
    monkeypatch.setattr(loop, "run", fake_run)
    monkeypatch.setattr("misty_engine.stream.brain.Brain", lambda *a, **k: None)
    facts.remember(ctx, "english task", "the Macbeth essay")

    async def confirm(q):
        asked.append(q)
        return False
    ctx.openrouter = ScriptBrain({"calls": [call("do", task="email my english task to Mr Tan")]},
                                 {"content": "I stopped before sending."})
    out, _, _ = await say(ctx, "email my english task to mr tan", confirm=confirm)
    assert asked == ["Send the email to Mr Tan?"]
    assert out["actions"][0] == {"tool": "do", "ok": False, "outcome": "blocked"}


@pytest.mark.asyncio
async def test_a_failed_call_still_answers_and_is_saved(ctx):
    class Down:
        async def post(self, *a, **k):
            from misty_engine.errors import NetworkDown
            raise NetworkDown()
    ctx.openrouter = Down()
    out, streamed, _ = await say(ctx, "hi")
    assert "offline" in streamed and out["reply"] == "I'm offline right now."
    assert [r["role"] for r in chat.history(ctx.con)] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_streamed_reply_folds_back_into_a_normal_one():
    sse = "\n".join([
        ": OPENROUTER PROCESSING", "",
        'data: {"choices":[{"delta":{"content":"Open"}}]}', "",
        'data: {"choices":[{"delta":{"content":"ing it"}}]}', "",
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"open_fact","arguments":"{\\"key\\""}}]}}]}', "",
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":": \\"english task\\"}"}}]},"finish_reason":"tool_calls"}]}', "",
        'data: {"choices":[],"usage":{"cost":0.0007}}', "", "data: [DONE]", ""])
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=sse))
    pieces = []

    async def d(p):
        pieces.append(p)
    async with httpx.AsyncClient(transport=transport) as c:
        async with c.stream("POST", "http://x/chat") as resp:
            data = await openrouter._read_stream(resp, d)
    msg = data["choices"][0]["message"]
    assert pieces == ["Open", "ing it"] and msg["content"] == "Opening it"
    assert msg["tool_calls"][0]["function"] == {"name": "open_fact", "arguments": '{"key": "english task"}'}
    assert data["usage"]["cost"] == 0.0007


@pytest.mark.asyncio
async def test_long_chat_compacts_into_a_summary_and_stays_small(ctx):
    for i in range(chat.KEEP + chat.COMPACT_EVERY):
        chat._save(ctx, "user" if i % 2 == 0 else "assistant", f"message {i} about the physics test on the 30th")
    ctx.openrouter = ScriptBrain({"content": "Isaac has a physics test on the 30th and is revising."})
    assert await chat.compact(ctx) is True
    text, through = chat.summary(ctx.con)
    assert "physics test" in text and through > 0
    assert len(chat.unsummarized(ctx.con)) == chat.KEEP
    body = ctx.openrouter.bodies[0]
    assert "tools" not in body and "message 0 about" in body["messages"][1]["content"]

    ctx.openrouter = ScriptBrain({"content": "Good luck on the 30th."})
    await say(ctx, "what's coming up?")
    sent = ctx.openrouter.bodies[0]["messages"]
    assert "Earlier in this chat (summary):\nIsaac has a physics test" in sys_text(sent[0])
    assert len(sent) == 1 + chat.KEEP + 1                       # system + recent + new, not the whole thread
    assert all("message 0 about" not in m["content"] for m in sent[1:])


@pytest.mark.asyncio
async def test_compaction_runs_by_itself_after_a_reply(ctx):
    import asyncio
    for i in range(chat.KEEP + chat.COMPACT_EVERY - 2):
        chat._save(ctx, "user" if i % 2 == 0 else "assistant", f"m{i}")
    ctx.openrouter = ScriptBrain({"content": "sure"}, {"content": "Summary: chatted."})
    await say(ctx, "hi")
    await asyncio.gather(*chat._BACKGROUND)
    assert chat.summary(ctx.con)[0] == "Summary: chatted."


@pytest.mark.asyncio
async def test_notes_for_the_message_are_attached_without_a_tool_call(ctx, monkeypatch):
    seen = []

    def notes(_ctx, q, budget):
        seen.append((q, budget))
        return "- Physics test: Friday 30 Oct, chapters 4-6"
    monkeypatch.setattr(chat, "_notes", notes)
    ctx.openrouter = ScriptBrain({"content": "Friday the 30th."})
    await say(ctx, "when's my physics test?")
    system = json.dumps(ctx.openrouter.bodies[0]["messages"])     # in the system message, or (setting on) in front of his line
    assert "From his notes (picked for this message)" in system and "Physics test: Friday 30 Oct" in system
    assert seen == [("when's my physics test?", chat.NOTES_BUDGET)]


def test_past_the_list_limit_only_the_closest_facts_go_in(ctx, monkeypatch):
    ctx.settings.set("user_name", "Isaac")             # the prompt now fills his name in from settings
    monkeypatch.setattr(facts, "LIST_MAX", 3)
    for k in ("english task", "physics tutor", "chem tutor", "spanish teacher", "bus route"):
        facts.remember(ctx, k, f"about {k}")
    monkeypatch.setattr(chat, "_notes", lambda *a: "")
    monkeypatch.setattr(chat, "FACTS_MATCHED", 2)
    system = chat.system_prompt(ctx, "who is my physics tutor")
    listed = system.split("Facts Isaac told you:\n")[1].split("\n\n")[0].splitlines()
    assert listed == ["- physics tutor: about physics tutor", "- chem tutor: about chem tutor"]


@pytest.mark.asyncio
async def test_asking_again_opens_again(ctx, opened):
    facts.remember(ctx, "english task", "the Macbeth essay", "https://docs.google.com/document/d/abc", "Macbeth essay")
    for _ in range(2):
        ctx.openrouter = ScriptBrain({"calls": [call("open_fact", key="english task")]}, {"content": "Opened it."})
        await say(ctx, "do my english task")
    assert len(opened) == 2
    system = sys_text(ctx.openrouter.bodies[0]["messages"][0])
    assert "even if you opened it earlier" in system and "unless a tool did it in this turn" in system


@pytest.mark.parametrize("text,want", [
    ("do my english test task", True), ("open my english test task", True), ("let's do the english test task", True),
    ("english test task pls", True), ("i wanna work on my english test task", True),
    ("pull up the english test thing", True), ("start my english test task", True), ("time for english test task", True),
    ("what is my english test task", False), ("my english test task is this page", False),
    ("forget my english test task", False), ("open my chemistry work", False), ("hi", False),
])
def test_wants_open_decides_in_code(ctx, text, want):
    facts.remember(ctx, "english test task", "the Macbeth page", "https://en.wikipedia.org/wiki/Macbeth", "Macbeth")
    facts.remember(ctx, "physics notes", "no link here")
    assert (facts.wants_open(ctx, text) is not None) is want


def test_two_equal_matches_leave_it_to_the_model(ctx):
    facts.remember(ctx, "english essay", "a", "https://x/1", "One")
    facts.remember(ctx, "english reading", "b", "https://x/2", "Two")
    assert facts.wants_open(ctx, "open my english stuff") is None


@pytest.mark.parametrize("text", ["Isaac's English task is the page he has open right now.",
                                  "Isaac's English task is this doc.", "English task: this page"])
def test_this_becomes_the_title(text):
    out = facts.name_this(text, "Macbeth essay")
    assert "\u201CMacbeth essay\u201D" in out and "right now" not in out and "this" not in out.lower()


# ---- voice, stop, look, calc

@pytest.mark.asyncio
async def test_voice_adds_the_spoken_style_and_a_short_cap(ctx):
    ctx.settings.set("user_name", "Isaac")             # the prompt now fills his name in from settings
    ctx.openrouter = ScriptBrain({"content": "Try factorising first."}, {"content": "Sure."})
    await chat.send(ctx, "help with q3", on_delta=nothing, on_status=nothing, confirm=no, voice=True)
    await say(ctx, "hi")
    spoken, typed = ctx.openrouter.bodies
    assert "Voice: Isaac is LISTENING to you" in sys_text(spoken["messages"][0])
    assert "one step at a time" in sys_text(spoken["messages"][0])
    assert "Voice:" not in sys_text(typed["messages"][0])
    assert spoken["max_tokens"] == typed["max_tokens"] == chat.DRAW_MAX_TOKENS     # room for a drawing's JSON; see the long-drawing test


@pytest.mark.asyncio
async def test_stop_mid_reply_saves_what_was_said(ctx):
    import asyncio
    stop = asyncio.Event()
    pieces = []

    async def d(p):
        pieces.append(p)
        if len(pieces) == 2:
            stop.set()
    ctx.openrouter = ScriptBrain({"content": "one two three four five"})
    out = await chat.send(ctx, "count", on_delta=d, on_status=nothing, confirm=no, stop=stop)
    assert out["stopped"] is True and out["reply"] == "one two"
    row = chat.history(ctx.con)[-1]
    assert row["text"] == "one two" and json.loads(row["meta"])["stopped"] is True


@pytest.mark.asyncio
async def test_stop_cuts_off_a_quiet_model_and_skips_later_rounds(ctx):
    import asyncio
    stop = asyncio.Event()

    class Slow:
        bodies = []

        async def post(self, *a, **k):
            self.bodies.append(1)
            await asyncio.sleep(10)
    ctx.openrouter = Slow()
    task = asyncio.create_task(chat.send(ctx, "think hard", on_delta=nothing, on_status=nothing, confirm=no, stop=stop))
    await asyncio.sleep(0.05)
    stop.set()
    out = await asyncio.wait_for(task, 1)
    assert out == {**out, "stopped": True, "reply": "(stopped)"} and len(Slow.bodies) == 1


@pytest.mark.asyncio
async def test_rpc_stop_works_while_the_send_holds_the_lock(ctx):
    import asyncio
    from misty_engine.api_chat import chat_methods
    events = []

    async def notify(method, params):
        events.append((method, params))
        if method == "chat.delta" and params["text"].startswith("two"):
            await rpc["chat.stop"]({"turn": "t1"})
    ctx.notify = notify
    rpc = chat_methods(ctx)
    ctx.openrouter = ScriptBrain({"content": "one two three four"})
    out = await rpc["chat.send"]({"text": "go", "turn": "t1", "voice": True})
    assert out["stopped"] is True and out["reply"] == "one two"
    assert events[-1] == ("chat.done", {"turn": "t1", "stopped": True})
    assert (await rpc["chat.stop"]({"turn": "t1"})) == {"ok": False}            # finished turns can't be stopped
    with pytest.raises(Fatal):
        await rpc["chat.send"]({"text": "go", "turn": "t2", "voice": "yes"})


@pytest.mark.parametrize("expr,want", [
    ("2+3*4", 14), ("(2+3)*4", 20), ("-3**2", -9), ("(-3)**2", 9), ("7/2", 3.5), ("7//2", 3), ("7%3", 1),
    ("0.17*340", 57.8), ("sqrt(16)", 4), ("sin(30)", 0.5), ("cos(60)", 0.5), ("tan(45)", 1), ("sinr(pi/2)", 1),
    ("asin(0.5)", 30), ("log(1000)", 3), ("ln(e)", 1), ("2^10", 1024), ("3×4÷2", 6), ("1/3", 0.333333333333),
])
def test_calc_gets_it_right(expr, want):
    assert chat.calc(expr) == pytest.approx(want)


@pytest.mark.parametrize("expr,why", [
    ("__import__('os').system('ls')", "only"), ("x+1", "only"), ("(1).real", "only"), ("open('f')", "only"),
    ("sqrt(4, 2)", "only"), ("9**9**9", "too big"), ("10**400", "too big"), ("1/0", "zero"),
    ("sqrt(-1)", "outside"), ("(-8)**0.5", "no real"), ("'a'*3", "only"), ("[1]*3", "only"), ("", "empty"),
    ("1+", "read"),
])
def test_calc_refuses_anything_but_arithmetic(expr, why):
    with pytest.raises(ValueError, match=why):
        chat.calc(expr)


@pytest.mark.asyncio
async def test_calc_tool_result_goes_back_to_the_model(ctx):
    ctx.openrouter = ScriptBrain({"calls": [call("calc", expr="0.17*340")]}, {"content": "That's 57.8."})
    out, _, _ = await say(ctx, "what's 17% of 340")
    tool = ctx.openrouter.bodies[1]["messages"][-1]
    assert tool["role"] == "tool" and '"result": 57.8' in tool["content"]
    assert "Use calc for any arithmetic" in sys_text(ctx.openrouter.bodies[0]["messages"][0])


class LookHands:
    connected = True

    def __init__(self, fail=False):
        from fakes_hands import FakeHands
        self.inner, self.fail = FakeHands(), fail

    async def _call(self, name):
        assert name == "last_app"
        return {"pid": 42, "name": "Preview", "bundle_id": "com.apple.Preview"}

    async def capture_window(self, pid):
        if self.fail:
            raise RuntimeError("no screen recording")
        return await self.inner.capture_window(pid)


@pytest.mark.asyncio
async def test_look_shows_the_next_round_the_screen_and_never_saves_it(ctx):
    hands = LookHands()
    ctx.extras = {"hands": hands}
    ctx.openrouter = ScriptBrain({"calls": [call("look")]}, {"content": "Step 2 drops a minus sign."})
    out, _, statuses = await say(ctx, "is my working right")
    assert hands.inner.calls == [("capture_window", 42)] and statuses == ["Looking at your screen…"]
    nxt = ctx.openrouter.bodies[1]["messages"]
    assert [m["role"] for m in nxt[-3:]] == ["assistant", "tool", "user"]
    assert nxt[-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "_image" not in nxt[-2]["content"] and "Preview" in nxt[-2]["content"]
    dump = json.dumps([dict(r) for r in ctx.con.execute("select * from chat_messages").fetchall()])
    assert "base64" not in dump and "iVBOR" not in dump              # no image bytes anywhere in the thread
    meta = json.loads(chat.history(ctx.con)[-1]["meta"])
    assert meta["calls"][0]["result"].startswith("looked at his screen: Preview")

    ctx.openrouter = ScriptBrain({"content": "ok"})
    await say(ctx, "thanks")
    replay = ctx.openrouter.bodies[0]["messages"]
    # later turns replay text, never the image
    assert not any(c.get("type") == "image_url" for m in replay[1:] if isinstance(m["content"], list) for c in m["content"])


@pytest.mark.asyncio
async def test_look_says_plainly_when_it_cant(ctx):
    for extras, want in [({}, "isn't connected"), ({"hands": LookHands(fail=True)}, "couldn't capture")]:
        ctx.extras = extras
        ctx.openrouter = ScriptBrain({"calls": [call("look")]}, {"content": "I can't see your screen."})
        out, _, _ = await say(ctx, "what's this")
        assert want in ctx.openrouter.bodies[1]["messages"][-1]["content"] and out["actions"][0]["ok"] is False
        assert not any(c.get("type") == "image_url" for m in ctx.openrouter.bodies[1]["messages"][1:] if isinstance(m["content"], list) for c in m["content"])


async def test_class_notes_tool_last_physics(ctx):
    from misty_engine import classes
    s = classes.start(ctx.con, ctx.clock, "Physics", None, None)["id"]
    classes.add_segment(ctx.con, s, 0, 5, "room", "projectile motion today")
    classes.end(ctx.con, ctx.clock, s)
    ctx.con.execute("UPDATE class_sessions SET status='noted', notes_json=? WHERE id=?",
                    (json.dumps({"summary": "Projectile motion: horizontal and vertical are independent.",
                                 "sections": [], "remember": [], "action_items": [], "gaps": []}), s))
    ctx.openrouter = ScriptBrain({"calls": [call("class_notes", which="last", subject="Physics")]},
                                 {"content": "Projectiles."})
    await say(ctx, "what did we do in physics today")
    tool = ctx.openrouter.bodies[1]["messages"][-1]
    assert tool["role"] == "tool" and "horizontal and vertical are independent" in tool["content"]
    assert "class_notes" in sys_text(ctx.openrouter.bodies[0]["messages"][0])


@pytest.mark.asyncio
async def test_follow_up_after_a_look_still_knows_the_question_and_that_the_picture_is_gone(ctx):
    """Isaac: 'help me with question 4' (screen), then 'explain it simpler'. The screenshot is never kept, so the next
    turn must be told the picture is gone and how to get it back, and the reply must have carried the question."""
    ctx.extras = {"hands": LookHands()}
    ctx.openrouter = ScriptBrain({"calls": [call("look")]}, {"content": "Q4: solve 3x^2 + 2 = 14. x is 2 or -2."})
    await say(ctx, "help me with question 4")
    ctx.openrouter = ScriptBrain({"content": "ok"})
    await say(ctx, "explain it in a less jargony way")
    replay = ctx.openrouter.bodies[0]["messages"]
    tool = next(m["content"] for m in replay if m["role"] == "tool")
    assert "picture is gone" in tool and "call look again" in tool
    sysm = sys_text(replay[0])
    assert "never say you can't see" in sysm.lower() and "same question" in sysm


# ---- draw: the explainer window

def _scene():
    return {"title": "Asymptotes", "steps": [
        {"say": "Here is f", "do": [{"t": "axes", "x": [-6, 10], "y": [-6, 12]},
                                    {"t": "curve", "f": "3x/(x-2)", "c": "blue", "label": "f(x)"}]},
        {"say": "It never touches these", "do": [{"t": "vline", "x": 2, "c": "red", "label": "x = 2"},
                                                  {"t": "hline", "y": 3, "c": "green", "label": "y = 3"}]}]}


def test_draw_samples_curves_and_breaks_them_at_the_asymptote():
    from misty_engine import draw
    out = draw.build(_scene())
    curve = out["steps"][0]["do"][1]
    assert len(curve["segs"]) == 2                                  # left of x = 2 and right of it, never joined
    assert max(p[0] for p in curve["segs"][0]) < 2 < min(p[0] for p in curve["segs"][1])
    assert out["view"] == {"x": [-6.0, 10.0], "y": [-6.0, 12.0]} and out["steps"][1]["do"][0]["x"] == 2.0
    assert all(abs(y) < 500 for seg in curve["segs"] for _, y in seg)


@pytest.mark.parametrize("bad, why", [
    ({"steps": []}, "steps"),
    ({"steps": [{"do": [{"t": "curve", "f": "__import__('os')"}]}]}, "nothing to draw|only"),
    ({"steps": [{"do": [{"t": "dot", "x": "a", "y": 1}]}]}, "number"),
    ({"steps": [{"do": [{"t": "blob"}]}]}, "needs t"),
    ({"steps": [{"do": [{"t": "axes"}]}]}, "nothing to draw"),
    ({"steps": [{"do": [{"t": "dot", "x": 1, "y": 1}] * 15}]}, "at most"),
])
def test_draw_rejects_with_a_reason_the_model_can_use(bad, why):
    import re
    from misty_engine import draw
    with pytest.raises(ValueError, match=why) as e:
        draw.build(bad)
    assert re.search(why, str(e.value)) and len(str(e.value)) < 140


@pytest.mark.asyncio
async def test_draw_tool_sends_the_scene_to_the_app_and_keeps_the_reply_small(ctx):
    sent = []

    async def notify(method, params):
        sent.append((method, params))
    ctx.notify = notify
    ctx.openrouter = ScriptBrain({"calls": [call("draw", scene=_scene())]}, {"content": "Both lines it never touches are drawn."})
    out, text, _ = await say(ctx, "show me f of x equals 3x over x minus 2")
    assert [m for m, _ in sent if m == "slate.show"] == ["slate.show"]
    scene = next(p for m, p in sent if m == "slate.show")
    assert scene["title"] == "Asymptotes" and len(scene["blocks"]) >= 2
    result = next(m["content"] for m in ctx.openrouter.bodies[1]["messages"] if m["role"] == "tool")
    assert "segs" not in result and '"steps": 2' in result            # the model never gets the sampled points back
    assert out["actions"][0]["tool"] == "draw" and out["actions"][0]["ok"]


def _long_scene(steps=8):
    """A real teaching drawing: eight captioned steps, a few things each (the most a drawing may have)."""
    return {"title": "Minimum price", "steps": [
        {"say": f"Step {i}: the floor sits above the market price, so sellers want more than buyers will pay",
         "do": [{"t": "axes", "x": [0, 10], "y": [0, 10], "xl": "quantity", "yl": "price"}] if i == 0 else
               [{"t": "curve", "f": f"{i}+x*0.5", "c": "blue", "label": f"line number {i}"},
                {"t": "text", "x": 1, "y": 9, "s": f"note {i}: surplus appears between the two quantities"},
                {"t": "hline", "y": i, "c": "red", "label": f"price floor {i}"}]} for i in range(steps)]}


class CutOffBrain(ScriptBrain):
    """A model that is stopped by max_tokens like the real one: a tool call longer than the cap arrives cut short."""
    async def post(self, path, body, **kw):
        out = await super().post(path, body, **kw)
        msg = out["choices"][0]["message"]
        for c in msg.get("tool_calls") or []:
            cut = int(body["max_tokens"] * 3.2)                    # about 3 characters of JSON per token
            c["function"]["arguments"] = c["function"]["arguments"][:cut]
        return out


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", [True, False])
async def test_a_long_drawing_is_not_cut_off_by_the_reply_cap(ctx, voice):
    sent = []

    async def notify(method, params):
        sent.append(method)
    ctx.notify = notify
    ctx.openrouter = CutOffBrain({"calls": [call("draw", scene=_long_scene())]}, {"content": "Done."})
    await chat.send(ctx, "show me how a minimum price works", on_delta=nothing, on_status=nothing, confirm=no, voice=voice)
    assert sent == ["slate.show"]                                  # the scene arrived whole, first try
    assert ctx.openrouter.bodies[-1]["max_tokens"] <= 700          # and the sentence after it is still kept short


@pytest.mark.asyncio
async def test_draw_tool_reports_a_bad_scene_so_the_model_can_fix_it(ctx):
    ctx.openrouter = ScriptBrain({"calls": [call("draw", scene={"steps": []})]}, {"content": "ok"})
    out, _, _ = await say(ctx, "draw something")
    assert out["actions"][0]["ok"] is False
    assert "steps" in next(m["content"] for m in ctx.openrouter.bodies[1]["messages"] if m["role"] == "tool")


@pytest.mark.asyncio
async def test_voice_turns_are_told_the_captions_are_read_aloud(ctx):
    ctx.openrouter = ScriptBrain({"content": "ok"})
    await chat.send(ctx, "draw a parabola", on_delta=nothing, on_status=nothing, confirm=no, voice=True)
    sysm = sys_text(ctx.openrouter.bodies[0]["messages"][0])
    assert "read aloud" in sysm and "one short sentence at most" in sysm
    ctx.openrouter = ScriptBrain({"content": "ok"})
    await say(ctx, "draw a parabola")                              # typed: no such rule
    assert "read aloud as the picture builds" not in sys_text(ctx.openrouter.bodies[0]["messages"][0])


async def test_exam_mode_is_read_only_and_ignores_the_thread(ctx):
    """Season 2 exam: no history in, nothing saved, write tools never offered, and refused if asked anyway."""
    ctx.openrouter = ScriptBrain({"content": "earlier"})
    await say(ctx, "an earlier message")
    before = ctx.con.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
    brain = ScriptBrain({"calls": [call("remember", key="x", text="y")]}, {"content": "answer"})
    ctx.openrouter = brain
    out = await chat.send(ctx, "what is due?", on_delta=nothing, on_status=nothing, confirm=no, exam=True)
    assert out["reply"] == "answer"
    assert ctx.con.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == before
    assert facts.get(ctx.con, "x") is None
    offered = {t["function"]["name"] for t in brain.bodies[0]["tools"]}
    assert offered == chat.EXAM_TOOLS
    assert not any(m.get("content") == "an earlier message" for m in brain.bodies[0]["messages"])


def test_costs_tool_sums_misty_spend_by_purpose(ctx):
    now = ctx.clock.now()
    with ctx.con:
        for p, u in (("chat", 0.01), ("chat", 0.02), ("class", 0.005)):
            ctx.con.execute("INSERT INTO spend(ts, provider, model, usd, purpose) VALUES (?,?,?,?,?)",
                            (now, "openrouter", "haiku", u, p))
    r = chat._costs(ctx, {"since": "today"})
    assert r["ok"] and r["calls"] == 3 and abs(r["total_usd"] - 0.035) < 1e-9
    assert r["by_purpose"][0]["purpose"] == "chat"
    assert chat._costs(ctx, {"since": "not a date"})["ok"] is False


@pytest.mark.asyncio
async def test_a_lead_in_before_a_tool_is_said_before_the_tool_runs(ctx, monkeypatch):
    """Voice: "Checking your calendar" ends the moment the tool call arrives, so it is spoken while the tool runs
    (not after, with the answer). The streamed pieces already end with a newline when the tool starts."""
    ctx.openrouter = ScriptBrain({"content": "Checking your calendar", "calls": [call("calc", expr="2+2")]},
                                 {"content": "That's 4."})
    pieces, at_tool = [], []

    async def d(p):
        pieces.append(p)
    real = chat.run_tool

    async def spy(ctx_, name, args, **kw):
        at_tool.append("".join(pieces))
        return await real(ctx_, name, args, **kw)
    monkeypatch.setattr(chat, "run_tool", spy)
    out = await chat.send(ctx, "what is 2+2 on my calendar", on_delta=d, on_status=nothing, confirm=no, voice=True)
    assert at_tool and at_tool[0].strip() == "Checking your calendar" and at_tool[0].endswith("\n")
    assert "".join(pieces).count("\n") == 1                      # no second newline when the next round starts
    assert out["reply"].endswith("That's 4.")


@pytest.mark.asyncio
async def test_a_tool_call_with_no_lead_in_adds_nothing(ctx):
    ctx.openrouter = ScriptBrain({"calls": [call("calc", expr="2+2")]}, {"content": "That's 4."})
    out, text, _ = await say(ctx, "what's 2+2")
    assert "\n" not in text and text.strip() == "That's 4."


async def _run_with_tool_taking(ctx, monkeypatch, seconds, voice):
    import asyncio
    monkeypatch.setattr(chat, "FILLER_AFTER", 0.05)
    ctx.openrouter = ScriptBrain({"calls": [call("calc", expr="2+2")]}, {"content": "That's 4."})

    async def slow(ctx_, name, args, **kw):
        await asyncio.sleep(seconds)
        return {"ok": True, "result": 4}
    monkeypatch.setattr(chat, "run_tool", slow)
    pieces = []

    async def d(p):
        pieces.append(p)
    out = await chat.send(ctx, "what's 2+2", on_delta=d, on_status=nothing, confirm=no, voice=voice)
    return out, "".join(pieces)


@pytest.mark.asyncio
async def test_a_slow_tool_on_a_voice_turn_gets_a_one_sec_filler(ctx, monkeypatch):
    out, text = await _run_with_tool_taking(ctx, monkeypatch, 0.3, voice=True)
    assert text.strip() == "One sec.\nThat's 4."


@pytest.mark.asyncio
async def test_a_quick_tool_gets_no_filler(ctx, monkeypatch):
    out, text = await _run_with_tool_taking(ctx, monkeypatch, 0.0, voice=True)
    assert text.strip() == "That's 4."


@pytest.mark.asyncio
async def test_typed_chat_never_gets_the_filler(ctx, monkeypatch):
    out, text = await _run_with_tool_taking(ctx, monkeypatch, 0.3, voice=False)
    assert text.strip() == "That's 4."


# ---- physics tutor: solve is a private, calc-checked answer key built from the screenshot

KEY = {"question": "A 2.0 kg ball falls 5.0 m from rest. Find its speed at the bottom.",
       "given": ["m = 2.0 kg", "h = 5.0 m", "u = 0"], "find": "v in m/s",
       "idea": "Energy is conserved: GPE lost becomes KE gained.",
       "steps": [{"say": "GPE lost = mgh", "formula": "Ep = mgh", "expr": "2.0*9.81*5.0", "unit": "J"},
                 {"say": "Set equal to 1/2 m v^2 and solve", "formula": "Ek = 1/2 mv^2",
                  "expr": "sqrt(2*9.81*5.0)", "unit": "m/s"}],
       "answer": {"expr": "sqrt(2*9.81*5.0)", "unit": "m/s", "sig_figs": 2},
       "check": "Mass cancels, so any mass gives the same v.",
       "mistakes": ["forgetting the square root"], "hints": ["What energy does it have at the top?", "b", "c"]}


@pytest.mark.asyncio
async def test_solve_sends_the_screenshot_thinks_and_rechecks_every_number(ctx):
    hands = LookHands()
    ctx.extras = {"hands": hands}
    ctx.openrouter = ScriptBrain({"content": "```json\n" + json.dumps(KEY) + "\n```"},
                                 {"content": "Ball falls 5.0 m, find v. What energy does it have at the top?"})
    out, text, statuses = await say(ctx, "Can you help me with this please?")   # Isaac's real words, 2026-11-14
    assert hands.inner.calls == [("capture_window", 42)]       # code looked: the model didn't have to choose to
    solver = ctx.openrouter.bodies[0]
    assert solver["reasoning"]["enabled"] is True and "physics" in solver["messages"][0]["content"].lower()
    assert solver["messages"][1]["content"][-1]["image_url"]["url"].startswith("data:image/")
    teach = ctx.openrouter.bodies[1]["messages"]
    assert teach[-1]["content"][1]["image_url"]["url"].startswith("data:image/")      # she sees the page too
    tool = json.loads(teach[-2]["content"])
    assert tool["ok"] and tool["key"]["steps"][0]["value"] == 98.1
    assert tool["key"]["answer"]["rounded"] == 9.9
    assert "Working the problem out…" in statuses and "What energy" in text
    assert "solve" in sys_text(ctx.openrouter.bodies[0]["messages"][0])
    meta = json.loads(chat.history(ctx.con)[-1]["meta"])
    solve_call = [c for c in meta["calls"] if c["name"] == "solve"][0]
    assert "9.9" in solve_call["result"] and "base64" not in solve_call["result"]   # the key outlives the turn

    ctx.openrouter = ScriptBrain({"content": "Right, so which energy is it at the top?"})
    await say(ctx, "I don't get this")                         # same question: no second look or solve
    assert hands.inner.calls == [("capture_window", 42)] and len(ctx.openrouter.bodies) == 1


def test_wants_help_on_real_phrases():
    from misty_engine.tutor import wants_help
    for t in ["Can you help me with this please?", "It's on my screen. Give you to question number one part D for me.",
              "I'm stuck on question 3", "how do I do this", "explain this problem"]:
        assert wants_help(t), t
    for t in ["can u pl open my physics clas", "tysm", "help me reply to this email", "what's on today",
              "help me open my essay"]:
        assert not wants_help(t), t
    assert not wants_help("I don't get this", solved_last_turn=True)
    assert wants_help("ok now help with part e", solved_last_turn=True)


@pytest.mark.asyncio
async def test_solve_says_plainly_when_the_key_is_garbled_or_not_physics(ctx):
    for reply, want in [("no json here", "garbled"), (json.dumps({"error": "not a physics problem"}), "not a physics")]:
        ctx.openrouter = ScriptBrain({"calls": [call("solve", problem="write my essay")]},
                                     {"content": reply}, {"content": "That's not physics."})
        out, _, _ = await say(ctx, "solve this")
        tool = json.loads(ctx.openrouter.bodies[2]["messages"][-1]["content"])
        assert tool["ok"] is False and want in tool["error"]


def test_solve_marks_a_sum_that_wont_run_instead_of_inventing_a_number():
    from misty_engine import tutor
    k = tutor._checked({"steps": [{"expr": "m*g*h"}], "answer": {"expr": "1/0", "sig_figs": 3}})
    assert "couldn't compute" in k["steps"][0]["value"] and "couldn't compute" in k["answer"]["value"]


@pytest.mark.asyncio
async def test_without_a_key_board_she_draws_her_own_once_and_thanks_ends_it(ctx):
    ctx.extras = {"hands": LookHands()}
    board = call("draw", scene={"title": "Ball", "steps": [{"say": "Givens", "do": [{"t": "text", "x": 0, "y": 0, "s": "m = 2.0 kg"}]}]})
    ctx.notify = lambda *a: asyncio.sleep(0)
    ctx.openrouter = ScriptBrain({"content": json.dumps(KEY)}, {"calls": [board]}, {"content": "Energy idea. Which energy at the top?"})
    await say(ctx, "Can you help me with this please?")
    b = ctx.openrouter.bodies
    assert b[1]["tool_choice"]["function"]["name"] == "draw" and b[2]["tool_choice"] == "none"
    ctx.openrouter = ScriptBrain({"content": "Yes, GPE. Which formula?"})
    await say(ctx, "gravitational potential energy?")
    assert ctx.openrouter.bodies[0]["tool_choice"] == "auto"                          # no key board: no forced judge
    assert len(ctx.openrouter.bodies) == 1                                            # no second look or solve
    ctx.openrouter = ScriptBrain({"content": "Anytime."})
    await say(ctx, "tysm")
    assert ctx.openrouter.bodies[0]["tool_choice"] == "auto"


BOARD_KEY = {**KEY, "board": {"title": "Falling ball", "steps": [
    {"say": "Here's the ball at the top, 5 metres up.", "do": [{"t": "axes", "x": [0, 10], "y": [0, 6], "bare": True},
                                                           {"t": "dot", "x": 3, "y": 5, "label": "2.0 kg"}]},
    {"say": "What energy does it have up here?", "ask": True, "do": [{"t": "text", "x": 4, "y": 5, "s": "top"}]},
    {"say": "Right, GPE, and it all turns into KE.", "do": [{"t": "seg", "x1": 3, "y1": 5, "x2": 3, "y2": 1, "arrow": True}]},
    {"say": "So which formula links them?", "ask": True, "do": [{"t": "eq", "lines": ["mgh = ½mv²"]}]},
    {"say": "v comes out at about 9.9 m/s.", "do": [{"t": "eq", "lines": ["v = 9.9 m/s"], "hi": 0}]}]}}


@pytest.mark.asyncio
async def test_the_key_draws_a_picture_board_and_it_only_moves_on_when_he_gets_it(ctx):
    ctx.extras = {"hands": LookHands()}
    sent = []

    async def notify(kind, payload):
        if kind in ("slate.show", "slate.append"):
            sent.append((kind, payload))
    ctx.notify = notify
    ctx.openrouter = ScriptBrain({"content": json.dumps(BOARD_KEY)}, {"content": "Think about where it starts. What energy?"})
    await say(ctx, "Can you help me with this please?")
    b = ctx.openrouter.bodies
    assert b[1]["tool_choice"] == "none"                                   # the board came from the key: she only talks
    first = sent[-1][1]
    n0 = len(first["blocks"])
    assert sent[-1][0] == "slate.show" and first["blocks"][-1]["k"] == "turn"                  # the question is a pause for him
    assert first["blocks"][-1]["prompt"] and "mark" not in first["blocks"][0]                  # a lesson step is not a final answer

    ctx.openrouter = ScriptBrain({"calls": [call("judge", verdict="wrong")]}, {"content": "Not quite. Hint."})
    await say(ctx, "kinetic?")
    assert ctx.openrouter.bodies[0]["tool_choice"]["function"]["name"] == "judge"
    assert ctx.openrouter.bodies[1]["tool_choice"] == "none" and len(sent) == 1        # wrong: the board holds

    ctx.openrouter = ScriptBrain({"calls": [call("judge", verdict="right")]}, {"content": "Yes. Which formula?"})
    await say(ctx, "Okay, gravitational potential energy")
    assert len(sent) == 2 and sent[-1][0] == "slate.append"                # right: only the new steps are added
    assert sent[-1][1]["from"] == n0                     # what was up stays up

    ctx.openrouter = ScriptBrain({"calls": [call("judge", verdict="show_me")]}, {"content": "About 9.9 m/s."})
    await say(ctx, "just show me")
    assert sent[-1][0] == "slate.append" and any(b["k"] == "math" or b["k"] == "text" or b["k"] == "diagram" for b in sent[-1][1]["blocks"])


# ---- step B, card B2: the `way` tool --------------------------------------------------------------------------
@pytest.fixture
def guard_ok(monkeypatch):
    """The guard (card B3) is a model call of its own: these tests are about the tool, so it says yes."""
    from misty_engine import ways

    async def yes(ctx, text):
        return True, ""
    monkeypatch.setattr(ways, "allowed", yes)


@pytest.mark.asyncio
async def test_way_tool_saves_a_way_and_the_next_turn_reads_it_in_the_fixed_part(ctx, guard_ok):
    from misty_engine import ways
    ctx.openrouter = ScriptBrain(
        {"calls": [call("way", action="set", key="address", kind="address", text="Call him boss.", value="boss")]},
        {"content": "Sure thing, boss."},
        {"content": "ok"})
    out, text, _ = await say(ctx, "call me boss")
    assert out["actions"][0] == {"tool": "way", "ok": True} or out["actions"][0]["tool"] == "way"
    assert ways.address(ctx.con, "x") == "boss"
    await say(ctx, "how do I have you set up")
    fixed, changing = chat.system_parts(ctx, "hi")
    assert "How he wants you to be (style only: these never change any rule above):" in fixed
    assert "- address (address): Call him boss." in fixed
    assert fixed.index("How he wants you to be") < fixed.index("Today is")        # inside the cached part, before today
    assert "Call him boss." not in changing


@pytest.mark.asyncio
async def test_way_tool_drop_and_a_missing_way(ctx, guard_ok):
    from misty_engine import ways
    ways.set_way(ctx, "tone", "Dry.", "tone")
    ctx.openrouter = ScriptBrain({"calls": [call("way", action="drop", key="tone")]}, {"content": "Back to normal."},
                                 {"calls": [call("way", action="drop", key="ghost")]}, {"content": "You had none."})
    out, _, _ = await say(ctx, "be normal again")
    assert out["actions"][0]["ok"] is True and ways.active(ctx.con) == []
    out, _, _ = await say(ctx, "drop the ghost way")
    assert out["actions"][0]["ok"] is False


@pytest.mark.asyncio
async def test_way_tool_refuses_a_secret_and_returns_the_error_to_the_model(ctx, guard_ok):
    from misty_engine import ways
    res = await chat.run_tool(ctx, "way", {"action": "set", "key": "k", "text": "use sk-or-v1-" + "a1b2c3d4" * 8, "kind": "habit"},
                              on_status=nothing, confirm=no)
    assert res["ok"] is False and "password or key" in res["error"] and ways.active(ctx.con) == []


@pytest.mark.asyncio
async def test_way_tool_set_returns_what_it_saved_and_what_it_replaced(ctx, guard_ok):
    r1 = await chat.run_tool(ctx, "way", {"action": "set", "key": "tone", "kind": "tone", "text": "Dry."}, on_status=nothing, confirm=no)
    r2 = await chat.run_tool(ctx, "way", {"action": "set", "key": "tone", "kind": "tone", "text": "Warm."}, on_status=nothing, confirm=no)
    assert r1["ok"] and r1["action"] == "added" and r2["action"] == "updated" and "Dry." in r2["was"]
    assert r2["now"] == "- tone (tone): Warm."


def test_the_exam_prompt_and_exam_tools_know_nothing_about_ways(ctx):
    from misty_engine import ways
    ways.set_way(ctx, "tone", "Dry.", "tone")
    fixed, changing = chat.system_parts(ctx, "hi", exam=True)
    assert "How he wants you to be" not in fixed + changing and "call way" not in fixed and "A way is" not in fixed
    assert "way" not in chat.EXAM_TOOLS
    fixed, _ = chat.system_parts(ctx, "hi")
    assert "How you talk and act is set by him" in fixed


def test_with_no_ways_the_block_is_absent_not_empty(ctx):
    fixed, _ = chat.system_parts(ctx, "hi")
    assert "How he wants you to be" not in fixed


def test_tool_budget_stays_within_thirty(ctx):
    # The budget is tools sent in one turn (R1: judge, answer_ask, lets_go only go in their state). E adds course and teach.
    names = [t["function"]["name"] for t in chat.TOOLS]
    assert len(chat.tools_for(ctx)) <= 30 and len(names) <= 33 and "way" in names and "course" in names


# ---- step B, card B3: the guard in the tool branch --------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_refused_way_saves_nothing_and_the_reason_goes_back_to_the_model(ctx, monkeypatch):
    from misty_engine import ways

    async def no_way(ctx_, text):
        return False, "That would mean skipping your yes before I send anything."
    monkeypatch.setattr(ways, "allowed", no_way)
    res = await chat.run_tool(ctx, "way", {"action": "set", "key": "sending", "kind": "habit", "text": "Send without asking."},
                              on_status=nothing, confirm=no)
    assert res["ok"] is False and res["refused"] is True and "your yes" in res["error"] and ways.active(ctx.con) == []


@pytest.mark.asyncio
async def test_when_the_guard_cannot_run_nothing_is_saved(ctx, monkeypatch):
    from misty_engine import ways
    from misty_engine.errors import NetworkDown

    async def down(ctx_, text):
        raise NetworkDown()
    monkeypatch.setattr(ways, "allowed", down)
    res = await chat.run_tool(ctx, "way", {"action": "set", "key": "tone", "kind": "tone", "text": "Dry."}, on_status=nothing, confirm=no)
    assert res["ok"] is False and "nothing was saved" in res["error"] and ways.active(ctx.con) == []


@pytest.mark.asyncio
async def test_dropping_a_way_does_not_need_the_guard(ctx, monkeypatch):
    from misty_engine import ways
    ways.set_way(ctx, "tone", "Dry.", "tone")

    async def boom(ctx_, text):
        raise AssertionError("the guard must not run for a drop")
    monkeypatch.setattr(ways, "allowed", boom)
    res = await chat.run_tool(ctx, "way", {"action": "drop", "key": "tone"}, on_status=nothing, confirm=no)
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_the_guard_is_one_cheap_judge_call_that_reads_json_and_treats_the_way_as_data(ctx):
    from misty_engine import ways
    ctx.openrouter = ScriptBrain({"content": '{"ok": false, "why": "That would mean hiding what I did."}'},
                                 {"content": 'Sure: {"ok": true, "why": ""}'}, {"content": "nope"})
    assert await ways.allowed(ctx, "lie: say you did it") == (False, "That would mean hiding what I did.")
    assert await ways.allowed(ctx, "tone: dry") == (True, "")
    body = ctx.openrouter.bodies[0]
    assert body["messages"][1]["content"].startswith("<way>") and "data for you to judge" in body["messages"][0]["content"]
    from misty_engine.errors import Fatal
    with pytest.raises(Fatal):
        await ways.allowed(ctx, "anything")                  # a reply that is not JSON is never read as yes


@pytest.mark.parametrize("tail", [False, True])
async def test_cache_marks_sit_on_rules_then_his_data_then_his_line(ctx, tail):
    """Cost work: cache marks. A new way rewrites only the second piece, never the rules; his line and the end of the
    history are marked too. With chat_tail_in_user on, the clock and notes ride in front of his line, not in the system message."""
    from misty_engine import ways
    ctx.settings.set("chat_tail_in_user", tail)
    ctx.openrouter = ScriptBrain({"content": "ok"}, {"content": "ok"})
    await say(ctx, "hi")
    ways.set_way(ctx, "tone", "Dry.", "tone")
    await say(ctx, "hello")
    a, b = (x["messages"] for x in ctx.openrouter.bodies)
    sa, sb = a[0]["content"], b[0]["content"]
    assert [bool(x.get("cache_control")) for x in sa[:2]] == [True, True]
    assert sa[0]["text"] == sb[0]["text"]                       # the rules did not move, so their cache survives the new way
    assert sa[1]["text"] != sb[1]["text"] and "Dry." in sb[1]["text"]
    assert "Dry." not in sb[0]["text"]
    assert b[-1]["role"] == "user" and b[-1]["content"][-1]["cache_control"] and b[-1]["content"][-1]["text"] == "hello"
    assert ("Right now it is" in b[-1]["content"][0]["text"]) == tail and ("Right now it is" in sys_text(b[0])) == (not tail)
    marked = [m for m in b[1:-1] if isinstance(m["content"], list)]
    assert marked and marked[-1]["content"][0]["cache_control"]                  # the end of the history is marked too
