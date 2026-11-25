"""D2: timers, shortcuts, Focus, windows, hide apps, priorities. Each move: run, verify-fails, repeat. No real shortcut ever runs."""
import pytest

from misty_engine import gate, moves, timers
from misty_engine.moves import work
from misty_engine.wiki import priorities


class Hands:
    connected = True

    def __init__(self, hide_visible=None, frames_ok=True):
        self.calls, self.hide_visible, self.frames_ok, self.set = [], hide_visible or [], frames_ok, {}

    async def _call(self, op, **a):
        self.calls.append((op, a))
        if op == "window_list":
            return {"windows": [{"pid": 1, "app": "Safari", "index": 0, "minimised": False, "fullscreen": False},
                                {"pid": 2, "app": "Notes", "index": 0, "minimised": False, "fullscreen": False},
                                {"pid": 3, "app": "Slack", "index": 0, "minimised": True, "fullscreen": False}]}
        if op == "screen_frame":
            return {"frame": [0, 0, 1000, 800]}
        if op == "set_window_frame":
            self.set[a["pid"]] = a["frame"]
            return {"done": True}
        if op == "window_frames":
            f = self.set.get(a["pid"], [0, 0, 1, 1])
            return {"frames": [f if self.frames_ok else [9, 9, 9, 9]]}
        if op == "hide_apps":
            return {"hidden": ["Safari"], "visible": self.hide_visible}


@pytest.fixture
def shell(monkeypatch):
    ran, names = [], ["Math Class", "Focus On", "Focus Off"]

    async def ex(*argv, timeout=60.0):
        ran.append(argv)
        if argv[:2] == ("shortcuts", "list"):
            return 0, "\n".join(names)
        return (1, "boom") if "Broken" in argv else (0, "")
    monkeypatch.setattr(work, "_exec", ex)
    return ran, names


# ---- timers
@pytest.mark.asyncio
async def test_timer_start_verified_repeat_and_stop(ctx):
    r = await moves.run(ctx, "timer_start", {"minutes": 25, "label": "maths"}, "t1")
    assert r["ok"] and r["verified"] and len(timers.active(ctx)) == 1
    again = await moves.run(ctx, "timer_start", {"minutes": 25, "label": "maths"}, "t1")
    assert again["repeat"] and len(timers.active(ctx)) == 1               # same call id: not twice
    await moves.run(ctx, "timer_start", {"minutes": 30, "label": "maths"}, "t2")
    assert len(timers.active(ctx)) == 1                                   # one per label
    nothing = await moves.run(ctx, "timer_stop", {"label": "chem"}, "t3")              # D10: nothing to stop is done, and said so
    assert nothing["ok"] and nothing["verified"] and "nothing to stop" in nothing["what"] and len(timers.active(ctx)) == 1
    s = await moves.run(ctx, "timer_stop", {"label": "maths"}, "t4")
    assert s["ok"] and s["verified"] and not timers.active(ctx)


@pytest.mark.asyncio
async def test_timer_bounds_and_verify_fails_when_it_is_already_over(ctx, monkeypatch):
    assert not (await moves.run(ctx, "timer_start", {"minutes": 0}, "b1"))["ok"]
    monkeypatch.setattr(timers, "get", lambda c, i: {"status": "done", "ends_at": 0})
    r = await moves.run(ctx, "timer_start", {"minutes": 5}, "b2")
    assert r["ok"] and not r["verified"] and "note" in r


@pytest.mark.asyncio
async def test_a_finished_timer_goes_through_the_gate_once(ctx):
    ctx.clock.t = 1_792_720_800.0                      # 10:00 SGT, not in quiet hours
    timers.start(ctx, 1, "maths")
    ctx.clock.t += 61
    gate.set_presence(ctx, 120.0)
    assert await timers.fire_due(ctx) == 1 and await timers.fire_due(ctx) == 0
    t = ctx.con.execute("SELECT * FROM told WHERE topic='timer'").fetchall()
    assert len(t) == 1 and "maths timer is up" in t[0]["text"]


@pytest.mark.asyncio
async def test_a_timer_in_class_waits_in_the_gate(ctx):
    m = timers.moment({"id": 9, "label": ""})
    assert m.own and m.urgency == "now" and m.text == "Your timer is up."


# ---- shortcuts
@pytest.mark.asyncio
async def test_shortcut_run_only_for_allowed_names_and_never_from_text(ctx, shell):
    ran, _ = shell
    r = await moves.run(ctx, "shortcut_run", {"name": "Math Class"}, "s1")
    assert not r["ok"] and "list of shortcuts" in r["what"] and not any(a[:2] == ("shortcuts", "run") for a in ran)
    ctx.settings.set("shortcut_allow", ["Math Class"])
    ok = await moves.run(ctx, "shortcut_run", {"name": "math class"}, "s2")
    assert ok["ok"] and ok["verified"] and ("shortcuts", "run", "Math Class") in ran
    assert "never runs" in (await moves.run(ctx, "shortcut_run", {"name": "Math Class"}, "s3", source="text"))["error"]
    assert (await moves.run(ctx, "shortcut_run", {"name": "Math Class"}, "s2"))["repeat"]


@pytest.mark.asyncio
async def test_shortcut_run_verify_fails_on_a_bad_exit(ctx, shell):
    ctx.settings.set("shortcut_allow", ["Broken"])
    r = await moves.run(ctx, "shortcut_run", {"name": "Broken"}, "s4")
    assert not r["ok"] and not r["verified"]


@pytest.mark.asyncio
async def test_shortcut_allow_asks_and_needs_a_real_shortcut(ctx, shell):
    async def yes(q): return True
    async def no(q): return False
    assert not (await moves.run(ctx, "shortcut_allow", {"name": "Nope", "allow": True}, "a1", yes))["ok"]
    assert (await moves.run(ctx, "shortcut_allow", {"name": "Math Class", "allow": True}, "a2", no))["declined"]
    assert ctx.settings.get("shortcut_allow") == []
    r = await moves.run(ctx, "shortcut_allow", {"name": "math class", "allow": True}, "a3", yes)
    assert r["verified"] and ctx.settings.get("shortcut_allow") == ["Math Class"]
    assert (await moves.run(ctx, "shortcut_allow", {"name": "Math Class", "allow": False}, "a4", yes))["verified"]
    assert ctx.settings.get("shortcut_allow") == []


# ---- focus
@pytest.mark.asyncio
async def test_focus_runs_his_shortcut_or_says_how_to_make_them(ctx, shell):
    ran, names = shell
    r = await moves.run(ctx, "focus", {"state": "on"}, "f1")
    assert r["ok"] and r["verified"] and ("shortcuts", "run", "Focus On") in ran
    names.clear()
    miss = await moves.run(ctx, "focus", {"state": "off"}, "f2")
    assert not miss["ok"] and "Shortcuts app" in miss["what"]
    assert not (await moves.run(ctx, "focus", {"state": "maybe"}, "f3"))["ok"]


# ---- windows
@pytest.mark.asyncio
async def test_windows_arrange_reads_the_frames_back(ctx):
    ctx.extras = {**(ctx.extras or {}), "hands": Hands()}
    r = await moves.run(ctx, "windows_arrange", {"layout": "left_right", "apps": "Safari, Notes"}, "w1")
    assert r["ok"] and r["verified"]
    h = ctx.extras["hands"]
    assert h.set[1] == [0, 0, 500, 800] and h.set[2] == [500, 0, 500, 800]
    assert (await moves.run(ctx, "windows_arrange", {"layout": "left_right", "apps": "Safari, Notes"}, "w1"))["repeat"]


@pytest.mark.asyncio
async def test_windows_arrange_bad_cases_and_unproved(ctx):
    ctx.extras = {**(ctx.extras or {}), "hands": Hands(frames_ok=False)}
    assert "takes 2" in (await moves.run(ctx, "windows_arrange", {"layout": "left_right", "apps": "Safari"}, "w2"))["what"]
    assert "no window" in (await moves.run(ctx, "windows_arrange", {"layout": "full", "apps": "Slack"}, "w3"))["what"]   # minimised
    r = await moves.run(ctx, "windows_arrange", {"layout": "full", "apps": "Safari"}, "w4")
    assert r["ok"] and not r["verified"]                                   # the window did not land: not "done"


# ---- hide apps
@pytest.mark.asyncio
async def test_apps_hide_verified_only_when_the_rest_are_hidden(ctx):
    ctx.extras = {**(ctx.extras or {}), "hands": Hands(hide_visible=["Notes"])}
    r = await moves.run(ctx, "apps_hide", {"except": "Notes"}, "h1")
    assert r["ok"] and r["verified"]
    assert ctx.extras["hands"].calls[-1] == ("hide_apps", {"except": ["Notes"]})
    ctx.extras["hands"].hide_visible = ["Notes", "Safari"]
    bad = await moves.run(ctx, "apps_hide", {"except": "Notes"}, "h2")
    assert bad["ok"] and not bad["verified"]


# ---- priorities
@pytest.mark.asyncio
async def test_priority_add_exists_and_is_not_doubled(ctx):
    from misty_engine.wiki.store import WikiStore
    ctx.wiki = WikiStore(ctx.paths.wiki, ctx.clock)
    ctx.wiki.init()
    r = await moves.run(ctx, "priority_add", {"text": "Finish the chem lab report", "due": "2026-11-20"}, "p1")
    assert r["ok"] and r["verified"]
    await moves.run(ctx, "priority_add", {"text": "Finish the chem lab report", "due": "2026-11-20"}, "p2")
    lines = priorities.get(ctx.wiki)["lines"]
    assert [l["text"] for l in lines] == ["Finish the chem lab report (due 2026-11-20)"]
    assert not (await moves.run(ctx, "priority_add", {"text": "x", "due": "Friday"}, "p3"))["ok"]
