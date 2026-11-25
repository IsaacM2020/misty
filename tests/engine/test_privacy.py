"""Step C6: the apps she can't see, changed by talking, seeded once from config, pushed to the app and read back."""
import pytest

from misty_engine import chat, privacy, setting


class Hands:
    connected = True

    def __init__(self, windows=None, hold=True):
        self.windows, self.held, self.hold, self.calls = windows or [], [], hold, []

    async def _call(self, op, **a):
        self.calls.append(op)
        if op == "window_list":
            return {"windows": self.windows}
        if op == "private_apps":
            if self.hold:
                self.held = list(a["bundles"])
            return {"bundles": sorted(self.held)}


def W(app, bundle):
    return {"app": app, "bundle_id": bundle}


def put(ctx, h):
    ctx.extras = {**(ctx.extras or {}), "hands": h}


def test_seed_once_from_config(ctx):
    assert ctx.settings.get("private_apps") is None
    n = privacy.seed_defaults(ctx)
    assert n >= 6 and "com.apple.keychainaccess" in privacy.bundles(ctx)
    ctx.settings.set("private_apps", [])
    assert privacy.seed_defaults(ctx) == 0 and privacy.apps(ctx) == []          # his empty list is his


@pytest.mark.asyncio
async def test_add_resolves_a_running_app_and_the_app_confirms(ctx):
    privacy.seed_defaults(ctx)
    h = Hands([W("WhatsApp", "net.whatsapp.WhatsApp"), W("Safari", "com.apple.Safari")]); put(ctx, h)
    r = await privacy.change(ctx, "add", "whatsapp")
    assert r["ok"] and r["verified"] and "net.whatsapp.WhatsApp" in h.held


@pytest.mark.asyncio
async def test_two_matches_asks_which(ctx):
    privacy.seed_defaults(ctx)
    put(ctx, Hands([W("Google Chrome", "com.google.Chrome"), W("Google Chrome Canary", "com.google.Chrome.canary")]))
    r = await privacy.change(ctx, "add", "chrome")
    assert not r["ok"] and len(r["which"]) == 2 and "com.google.Chrome" not in privacy.bundles(ctx)


@pytest.mark.asyncio
async def test_not_running_and_remove(ctx):
    privacy.seed_defaults(ctx)
    h = Hands([W("Notes", "com.apple.Notes")]); put(ctx, h)
    assert not (await privacy.change(ctx, "add", "Signal"))["ok"]
    assert (await privacy.change(ctx, "add", "Notes"))["ok"]
    r = await privacy.change(ctx, "remove", "notes")
    assert r["ok"] and "com.apple.Notes" not in h.held
    assert not (await privacy.change(ctx, "remove", "notes"))["ok"]


@pytest.mark.asyncio
async def test_unconfirmed_push_is_not_verified(ctx):
    privacy.seed_defaults(ctx)
    put(ctx, Hands([W("Notes", "com.apple.Notes")], hold=False))
    r = await privacy.change(ctx, "add", "Notes")
    assert r["ok"] and r["verified"] is False


@pytest.mark.asyncio
async def test_through_the_setting_tool(ctx):
    privacy.seed_defaults(ctx)
    put(ctx, Hands([W("WhatsApp", "net.whatsapp.WhatsApp")]))
    async def no(_): return False
    r = await setting.apply(ctx, "private_app", None, no, "add", "WhatsApp")
    assert r["ok"] and r["verified"]


def test_tool_count_is_31_after_e2():
    assert len(chat.TOOLS) == 33
