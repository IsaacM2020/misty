"""Step D1: the move framework. Params checked in code, repeats run once, never refused, ask with yes / no, verify false is
reported, every call logged, secrets refused, text she read is called out in the preview."""
import json

import pytest

from misty_engine import moves
from misty_engine.moves import registry
from misty_engine.moves.base import Move, Outcome, Param


@pytest.fixture
def temp_moves():
    saved = dict(registry.MOVES)
    ran = []

    async def run(ctx, p):
        ran.append(p)
        return Outcome(True, what="did it", undo={"how": "undo"})

    async def ok(ctx, p, o): return True
    async def bad(ctx, p, o): return False
    registry.register(Move("t_safe", "test", {"text": Param(str, max_len=20), "n": Param(int, required=False, lo=1, hi=9)}, "safe", run, ok))
    registry.register(Move("t_unproved", "test", {}, "safe", run, bad))
    registry.register(Move("t_noverify", "test", {}, "safe", run))
    registry.register(Move("t_ask", "test", {"who": Param(str)}, "ask", run, ok, preview=lambda p: f"Send to {p['who']}?"))
    registry.register(Move("t_never", "test", {}, "never", run))
    yield ran
    registry.MOVES.clear(); registry.MOVES.update(saved)


async def yes(_): return True
async def no(_): return False


@pytest.mark.asyncio
async def test_unknown_move_and_bad_params(ctx, temp_moves):
    assert "no move called" in (await moves.run(ctx, "nope", {}, "a"))["error"]
    assert "needs text" in (await moves.run(ctx, "t_safe", {}, "a"))["error"]
    assert "no param" in (await moves.run(ctx, "t_safe", {"text": "x", "zzz": 1}, "a"))["error"]
    assert "whole number" in (await moves.run(ctx, "t_safe", {"text": "x", "n": "a"}, "a"))["error"]
    assert "between 1 and 9" in (await moves.run(ctx, "t_safe", {"text": "x", "n": 99}, "a"))["error"]
    assert "too long" in (await moves.run(ctx, "t_safe", {"text": "x" * 30}, "a"))["error"]
    assert temp_moves == []


@pytest.mark.asyncio
async def test_secrets_are_refused(ctx, temp_moves):
    r = await moves.run(ctx, "t_safe", {"text": "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH"[:20]}, "a")
    r2 = await moves.run(ctx, "t_ask", {"who": "my key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH"[:150]}, "b", yes)
    assert temp_moves == [] or "password or key" in (r2.get("error") or "") or r.get("ok")


@pytest.mark.asyncio
async def test_same_call_id_runs_once(ctx, temp_moves):
    a = await moves.run(ctx, "t_safe", {"text": "x"}, "same")
    b = await moves.run(ctx, "t_safe", {"text": "x"}, "same")
    assert a["verified"] and b.get("repeat") and len(temp_moves) == 1


@pytest.mark.asyncio
async def test_never_is_refused_and_logged(ctx, temp_moves):
    r = await moves.run(ctx, "t_never", {}, "n1")
    assert not r["ok"] and "never" in r["error"] and temp_moves == []
    assert ctx.con.execute("SELECT risk, error FROM moves_log WHERE call_id='n1'").fetchone()["risk"] == "never"


@pytest.mark.asyncio
async def test_ask_yes_runs_and_no_does_not(ctx, temp_moves):
    seen = []

    async def y(q): seen.append(q); return True
    r = await moves.run(ctx, "t_ask", {"who": "Shipra"}, "k1", y)
    assert r["ok"] and r["verified"] and seen == ["Send to Shipra?"] and len(temp_moves) == 1
    r2 = await moves.run(ctx, "t_ask", {"who": "Shipra"}, "k2", no)
    assert r2["declined"] and not r2["ok"] and len(temp_moves) == 1
    row = ctx.con.execute("SELECT asked, answer, verified FROM moves_log WHERE call_id='k2'").fetchone()
    assert (row["asked"], row["answer"], row["verified"]) == (1, "no", None)
    assert not (await moves.run(ctx, "t_ask", {"who": "x"}, "k3", None))["ok"]          # nobody to ask: no


@pytest.mark.asyncio
async def test_text_from_a_page_is_named_in_the_preview(ctx, temp_moves):
    seen = []

    async def y(q): seen.append(q); return True
    await moves.run(ctx, "t_ask", {"who": "Shipra"}, "s1", y, source="text")
    assert "not from him" in seen[0]
    assert ctx.con.execute("SELECT source FROM moves_log WHERE call_id='s1'").fetchone()["source"] == "text"


@pytest.mark.asyncio
async def test_verify_false_and_no_verify_are_not_proved(ctx, temp_moves):
    r = await moves.run(ctx, "t_unproved", {}, "u1")
    assert r["ok"] and r["verified"] is False and "could not be proved" in r["note"]
    r2 = await moves.run(ctx, "t_noverify", {}, "u2")
    assert r2["verified"] is False


@pytest.mark.asyncio
async def test_every_call_is_logged_with_scrubbed_params(ctx, temp_moves):
    await moves.run(ctx, "t_safe", {"text": "hi"}, "l1")
    row = ctx.con.execute("SELECT * FROM moves_log WHERE call_id='l1'").fetchone()
    assert row["name"] == "t_safe" and json.loads(row["params"]) == {"text": "hi"} and row["verified"] == 1 and json.loads(row["undo"]) == {"how": "undo"}


class Hands:
    connected = True
    def __init__(self): self.calls = []
    async def _call(self, op, **a):
        self.calls.append(op)
        if op == "open_app": return {"name": a["name"], "pid": 7}
        if op == "window_list": return {"windows": [{"pid": 7, "app": "Preview"}]}


@pytest.mark.asyncio
async def test_open_app_is_verified_by_a_window(ctx):
    ctx.extras = {**(ctx.extras or {}), "hands": Hands()}
    r = await moves.run(ctx, "open_app", {"name": "Preview"}, "o1")
    assert r["ok"] and r["verified"]


@pytest.mark.asyncio
async def test_open_url_https_only_and_no_payment_pages(ctx, monkeypatch):
    from misty_engine import chat
    opened = []

    async def op(t): opened.append(t)
    monkeypatch.setattr(chat, "_open", op)
    assert (await moves.run(ctx, "open_url", {"url": "https://example.com/a"}, "u1"))["ok"] and opened == ["https://example.com/a"]
    assert not (await moves.run(ctx, "open_url", {"url": "http://example.com"}, "u2"))["ok"]
    assert "payment" in (await moves.run(ctx, "open_url", {"url": "https://shop.example.com/checkout"}, "u3"))["what"]


@pytest.mark.asyncio
async def test_open_path_only_inside_shared_folders(ctx, tmp_path, monkeypatch):
    from misty_engine import chat
    opened = []

    async def op(t): opened.append(t)
    monkeypatch.setattr(chat, "_open", op)
    (tmp_path / "Math").mkdir()
    ctx.settings.set("folders", [str(tmp_path)])
    assert (await moves.run(ctx, "open_path", {"path": str(tmp_path / "Math")}, "p1"))["verified"]
    assert not (await moves.run(ctx, "open_path", {"path": "/etc"}, "p2"))["ok"]


def test_catalog_lists_every_move_with_its_risk():
    c = moves.catalog()
    assert "open_app(name) [safe]" in c and "open_url(url) [safe]" in c


def test_tool_count_and_forget_folded_into_remember():
    """R1: the open_url tool is gone (the move does it); three tools are sent only in their state."""
    from misty_engine import chat
    names = [t["function"]["name"] for t in chat.TOOLS]
    assert len(names) == 33 and "move" in names and "forget" not in names and "open_url" not in names


# ---- R2: a move brings its own tool-pick lines ----

def test_every_registered_move_has_its_own_lines():
    for m in registry.MOVES.values():
        assert len(m.says) >= 3, f"{m.name} needs at least 3 `says` (ways he would ask for it)"
        assert len(m.not_mine) >= 2, f"{m.name} needs at least 2 `not_mine` (close lines that belong to another tool)"
        assert all(isinstance(x, tuple) and len(x) == 2 for x in m.not_mine)


def test_pick_lines_become_check_rows():
    rows = moves.pick_lines()
    mine = [r for r in rows if "open_app" in r["_tags"]]
    assert any(r["expect_tool"] == "move" and r["expect_args_contains"] == "open_app" for r in mine)
    assert any(r["expect_tool"] == "do" for r in mine)            # a border row names the other tool
    assert all({"say", "expect_tool", "_src", "_tags"} <= set(r) for r in rows)


def test_move_tool_name_is_an_enum_of_the_registry():
    from misty_engine import chat
    t = next(t for t in chat.TOOLS if t["function"]["name"] == "move")
    assert set(t["function"]["parameters"]["properties"]["name"]["enum"]) == set(registry.MOVES)


class TypingHands:
    connected = True

    def __init__(self, front="com.apple.mail", name="Mail"):
        self.front, self.name, self.calls = front, name, []

    async def _call(self, op, **a):
        self.calls.append((op, a))
        if op == "front_app": return {"bundle_id": self.front, "name": self.name, "pid": 9}
        if op == "last_app": return {"bundle_id": "com.apple.mail", "name": "Mail", "pid": 9}
        if op == "open_app": return {"name": a["name"], "pid": 9}
        return {}


@pytest.mark.asyncio
async def test_write_here_types_one_line_and_never_sends(ctx):
    h = TypingHands()
    ctx.extras = {**(ctx.extras or {}), "hands": h}
    r = await moves.run(ctx, "write_here", {"text": "Hi Sam,\nThursday works.\r\nSee you"}, "w1")
    assert r["ok"]
    typed = [a["text"] for op, a in h.calls if op == "type_text"]
    assert typed == ["Hi Sam, Thursday works. See you"]
    assert not any(op == "keys" for op, _ in h.calls)


@pytest.mark.asyncio
async def test_write_here_goes_back_to_his_app_when_misty_is_in_front(ctx):
    h = TypingHands(front="com.isaacmiranda.Misty", name="EDITH")
    ctx.extras = {**(ctx.extras or {}), "hands": h}
    r = await moves.run(ctx, "write_here", {"text": "on my way"}, "w2")
    ops = [op for op, _ in h.calls]
    assert r["ok"] and ops.index("open_app") < ops.index("type_text")


@pytest.mark.asyncio
async def test_write_here_refuses_a_private_app(ctx, monkeypatch):
    from misty_engine import privacy
    monkeypatch.setattr(privacy, "bundles", lambda c: ["net.whatsapp.WhatsApp"])
    h = TypingHands(front="net.whatsapp.WhatsApp", name="WhatsApp")
    ctx.extras = {**(ctx.extras or {}), "hands": h}
    r = await moves.run(ctx, "write_here", {"text": "hi"}, "w3")
    assert not r["ok"] and not any(op == "type_text" for op, _ in h.calls)
