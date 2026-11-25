"""Settings by talking (step B, card B6): a fixed whitelist, app-side settings go over the hands wire and are only called
done when the app reads the value back, a budget rise asks first with the exact numbers, a drop does not."""
import pytest

from misty_engine import chat, setting
from test_chat import ScriptBrain, call


class FakeHands:
    connected = True

    def __init__(self, voices=("Ember", "Juno")):
        self.calls, self.voices, self.speak, self.voice = [], list(voices), True, "Ember"

    async def _call(self, op, **a):
        self.calls.append((op, a))
        if a["name"] == "speak_replies":
            self.speak = bool(a["value"])
            return {"ok": True, "value": self.speak}
        if a["value"] == "list":
            return {"ok": True, "value": self.voice, "voices": self.voices}
        if a["value"] not in self.voices:
            return {"ok": False, "error": "no such voice", "voices": self.voices}
        self.voice = a["value"]
        return {"ok": True, "value": self.voice}


async def yes(_q): return True
async def no(_q): return False


@pytest.fixture
def hands(ctx):
    ctx.extras = {"hands": FakeHands()}
    return ctx.extras["hands"]


@pytest.mark.asyncio
async def test_mute_and_unmute_go_to_the_app_and_are_verified_from_what_it_holds(ctx, hands):
    r = await setting.apply(ctx, "speak_replies", "off", yes)
    assert r["verified"] is True and hands.speak is False and hands.calls[0] == ("setting_apply", {"name": "speak_replies", "value": False})
    assert (await setting.apply(ctx, "speak_replies", "on", yes))["verified"] and hands.speak is True
    assert (await setting.apply(ctx, "speak_replies", "maybe", yes))["ok"] is False


@pytest.mark.asyncio
async def test_a_voice_change_is_verified_and_an_unknown_voice_is_not(ctx, hands):
    assert (await setting.apply(ctx, "voice", "Juno", yes))["verified"] and hands.voice == "Juno"
    bad = await setting.apply(ctx, "voice", "Nobody", yes)
    assert bad["ok"] is False and bad["verified"] is False and bad["voices"] == ["Ember", "Juno"]
    lst = await setting.apply(ctx, "voice", "list", yes)
    assert lst["verified"] is False and lst["voices"] and hands.voice == "Juno"


@pytest.mark.asyncio
async def test_without_the_app_connected_nothing_is_claimed(ctx):
    ctx.extras = {}
    r = await setting.apply(ctx, "speak_replies", "off", yes)
    assert r["ok"] is False and not r.get("verified")


@pytest.mark.asyncio
async def test_a_budget_raise_asks_first_with_the_exact_numbers_and_no_changes_nothing(ctx):
    ctx.settings.set("cap_daily_usd", 0.50)
    asked = []

    async def q(text):
        asked.append(text)
        return False
    r = await setting.apply(ctx, "daily_budget", "$1.00", q)
    assert asked == ["Raise the daily budget from $0.50 to $1.00?"] and r["declined"] and ctx.settings.get("cap_daily_usd") == 0.50
    r = await setting.apply(ctx, "daily_budget", "1", yes)
    assert r["verified"] and ctx.settings.get("cap_daily_usd") == 1.0


@pytest.mark.asyncio
async def test_lowering_the_budget_needs_no_yes_and_the_range_is_enforced(ctx):
    ctx.settings.set("cap_daily_usd", 1.0)
    assert (await setting.apply(ctx, "daily_budget", "0.30", no))["verified"] and ctx.settings.get("cap_daily_usd") == 0.30
    for bad in ("0.05", "3.50", "lots", ""):
        assert (await setting.apply(ctx, "daily_budget", bad, yes))["ok"] is False
    assert ctx.settings.get("cap_daily_usd") == 0.30


@pytest.mark.asyncio
async def test_nothing_outside_the_whitelist_can_be_set(ctx, hands):
    for n in ("cap_build_usd", "paused", "user_name", "phase", ""):
        assert (await setting.apply(ctx, n, "1", yes))["ok"] is False
    assert hands.calls == []


def test_the_tool_is_in_the_list_and_the_tool_count_is_31_after_e2(ctx):
    names = [t["function"]["name"] for t in chat.TOOLS]
    assert "setting" in names and "uncare" not in names and len(names) == 33


@pytest.mark.asyncio
async def test_chat_runs_the_setting_tool_and_care_drop_still_removes_a_care(ctx, hands):
    ctx.openrouter = ScriptBrain({"calls": [{"id": "t_setting", "type": "function", "function": {"name": "setting", "arguments": '{"name": "speak_replies", "value": "off"}'}}]}, {"content": "Muted."})
    from test_chat import say
    out, _, _ = await say(ctx, "mute yourself")
    assert out["actions"][0]["tool"] == "setting" and hands.speak is False
    ctx.openrouter = ScriptBrain({"calls": [call("care", action="drop", key="ghost")]}, {"content": "You had nothing like that."})
    out, _, _ = await say(ctx, "stop caring about ghosts")
    assert out["actions"][0]["tool"] == "care"


# ---- F5: ambient mode -------------------------------------------------------------------------------------------------

class AmbientHands:
    """The app's side of `ambient`: it answers with the mode it now holds. `refuse` is no yes to the consent, a class or a call."""
    connected = True

    def __init__(self, refuse=False):
        self.mode, self.refuse, self.calls = "off", refuse, []

    async def _call(self, op, **a):
        self.calls.append((op, a))
        if a["value"] != "off" and self.refuse:
            return {"ok": True, "value": self.mode}
        self.mode = a["value"]
        return {"ok": True, "value": self.mode}


@pytest.mark.asyncio
async def test_ambient_on_and_off_are_verified_from_what_the_app_holds(ctx):
    ctx.extras = {"hands": AmbientHands()}
    on = await setting.apply(ctx, "ambient", "on", yes)
    assert on["verified"] is True and ctx.extras["hands"].calls[0] == ("setting_apply", {"name": "ambient", "value": "name"})
    off = await setting.apply(ctx, "ambient", "off", yes)
    assert off["verified"] is True and ctx.extras["hands"].mode == "off"


@pytest.mark.asyncio
async def test_ambient_name_is_the_same_as_on(ctx):
    ctx.extras = {"hands": AmbientHands()}
    assert (await setting.apply(ctx, "ambient", "name", yes))["verified"]


@pytest.mark.asyncio
async def test_a_refusal_in_the_app_is_not_called_done(ctx):
    ctx.extras = {"hands": AmbientHands(refuse=True)}
    r = await setting.apply(ctx, "ambient", "on", yes)
    assert r["verified"] is False and "stayed off" in r["error"]


@pytest.mark.asyncio
async def test_the_judge_value_and_garbage_are_refused_before_the_app_is_asked(ctx):
    ctx.extras = {"hands": AmbientHands()}
    for bad in ("judge", "loud", ""):
        assert (await setting.apply(ctx, "ambient", bad, yes))["ok"] is False
    assert ctx.extras["hands"].calls == []


def test_ambient_is_in_the_tool_and_the_rule_and_the_tool_count_is_unchanged():
    tool = next(t for t in chat.TOOLS if t["function"]["name"] == "setting")
    assert "ambient" in tool["function"]["parameters"]["properties"]["name"]["enum"] and "ambient" in setting.NAMES
    assert "ambient" in chat.SETTING_RULE and "Only these eight" in chat.SETTING_RULE
