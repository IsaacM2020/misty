"""Stream core, offline: the observation list, the gate, and the loop driven by a scripted brain on FakeHands."""
from __future__ import annotations

import pytest

from fakes_hands import FakeHands, node, textedit
from misty_engine.stream import gate, loop, observe
from misty_engine.stream.bridge import AppInfo, MenuItem
from misty_engine.stream.brain import Decision


class ScriptBrain:
    name = "script"

    def __init__(self, acts):
        self.acts, self.seen = list(acts), []

    def messages(self, task, context, card, obs_text, history, image, nudge=""):
        self.seen.append({"obs": obs_text, "history": list(history), "nudge": nudge, "card": card})
        return []

    async def decide(self, msgs):
        return Decision(self.acts.pop(0), 0.0004, 0.01, 2000)


def mail():
    info = AppInfo("com.apple.mail", 777, "Mail")
    return info, [node("a1", "AXWindow", "New Message", actions=(), depth=0),
                  node("a2", "AXTextField", "To", actions=("AXSetValue",)),
                  node("a3", "AXButton", "Send")]


def test_render_numbers_and_hides_secrets():
    info, tree = textedit()
    tree.append(node("a9", "AXTextField", "Password", value="hunter2", secure=True, actions=("AXSetValue",)))
    o = observe.build(info, [FakeHands._snap(n) for n in tree])
    text = o.render()
    assert '[3] button "Save"' in text
    assert "hunter2" not in text and "(password field)" in text


def test_gate():
    assert gate.check("press", label="Send").kind == "ask"
    assert gate.check("press", label="Sender details").kind == "ok"
    assert gate.check("menu", label="Move to Trash").kind == "ask"
    assert gate.check("shortcut", keys="Command+Return").kind == "ask"
    assert gate.check("shortcut", keys="cmd+t").kind == "ok"
    assert gate.check("type_into", text="4111 1111 1111 1111").kind == "never"
    assert gate.check("type_into", text="hi", secure_field=True).kind == "never"


async def test_loop_presses_by_element_and_finishes(tmp_path):
    h = FakeHands({"TextEdit": textedit()})
    h.menus = {501: {(): []}}
    def changed(kind, *args):
        if kind == "ax_set_value":
            h.apps["TextEdit"][1].append(node("a7", "AXStaticText", "hello world", actions=()))
    h.on_action = changed
    b = ScriptBrain([{"action": "type_into", "id": 2, "text": "hello world", "replace": True},
                     {"action": "done", "evidence": "text shows hello world"}])
    r = await loop.run("write hello world", hands=h, brain=b, home=tmp_path)
    assert r.outcome == "done" and r.raw_steps == 0
    assert h.values["a2"] == "hello world"
    assert not any(c[0] == "pointer" for c in h.calls)          # the real mouse never moved
    assert (tmp_path / "stream" / "runs").exists()


async def test_send_asks_and_stops_on_no(tmp_path):
    h = FakeHands({"Mail": mail()})
    h.menus = {777: {(): []}}
    b = ScriptBrain([{"action": "press", "id": 3}])
    asked = []
    async def confirm(q):
        asked.append(q); return False
    r = await loop.run("email mr sanu", hands=h, brain=b, home=tmp_path, confirm=confirm)
    assert r.outcome == "denied" and asked and "Send" in asked[0]
    assert h.performed == []


async def test_send_runs_on_yes(tmp_path):
    h = FakeHands({"Mail": mail()})
    h.menus = {777: {(): []}}
    b = ScriptBrain([{"action": "press", "id": 3}, {"action": "done", "evidence": "sent"}, {"action": "done", "evidence": "sent"}])
    async def yes(q):
        return True
    r = await loop.run("send it", hands=h, brain=b, home=tmp_path, confirm=yes)
    assert r.outcome == "done"
    assert h.performed == [(777, "a3", "AXPress")]


async def test_done_after_no_change_is_questioned_once(tmp_path):
    h = FakeHands({"TextEdit": textedit()})
    h.menus = {501: {(): []}}
    b = ScriptBrain([{"action": "press", "id": 3}, {"action": "done", "evidence": "?"}, {"action": "done", "evidence": "sure"}])
    r = await loop.run("save", hands=h, brain=b, home=tmp_path)
    assert r.outcome == "done" and r.steps == 3
    assert "changed nothing" in b.seen[2]["nudge"]


async def test_app_card_lists_menu_shortcuts(tmp_path):
    h = FakeHands({"TextEdit": textedit()})
    h.menus = {501: {(): [MenuItem("TextEdit", "", True), MenuItem("File", "", True)],
                     ("File",): [MenuItem("New", "Meta+N", True), MenuItem("Print…", "Meta+P", False)]}}
    b = ScriptBrain([{"action": "ask_user", "text": "which file?"}])
    r = await loop.run("x", hands=h, brain=b, home=tmp_path)
    assert "File: New (cmd+n)" in b.seen[0]["card"] and "Print" not in b.seen[0]["card"]
    assert r.outcome == "ask_user"


async def test_raw_input_refused_when_app_wont_come_front(tmp_path, monkeypatch):
    """The 2026-10-21 bug: keys meant for Notes landed in Finder. Now nothing is sent unless the target is in front."""
    import subprocess
    from misty_engine.stream import actions
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b""))
    monkeypatch.setattr(actions.asyncio, "sleep", lambda s: asyncio_noop())
    finder = AppInfo("com.apple.finder", 100, "Finder")
    h = FakeHands({"Finder": (finder, [node("f1", "AXWindow", "Downloads", actions=(), depth=0)]), "Notes": notes()}, front="Finder")
    o = observe.build(notes()[0], [FakeHands._snap(n) for n in notes()[1]])
    out = await actions.run({"action": "type_into", "id": 2, "text": "hi", "submit": True}, o, h, None)
    assert "would not come to the front" in out.text
    assert not any(c[0] in ("keys", "type_text", "pointer") for c in h.calls)


def notes():
    info = AppInfo("com.apple.Notes", 300, "Notes")
    return info, [node("n1", "AXWindow", "Notes", actions=(), depth=0), node("n2", "AXSearchField", "Search", actions=("AXSetValue",))]


async def asyncio_noop():
    return None


async def test_clear_desktop_minimises_every_window_but_mistys(tmp_path):
    from misty_engine.stream.bridge import WindowInfo
    h = FakeHands({"TextEdit": textedit()})
    h.menus = {501: {(): []}}
    h.windows = [WindowInfo(501, "com.apple.TextEdit", "TextEdit", 0, "a", (0, 0, 9, 9), False, False),
                 WindowInfo(9, "net.whatsapp.WhatsApp", "WhatsApp", 0, "w", (0, 0, 9, 9), False, True),
                 WindowInfo(5, "com.isaacmiranda.Misty", "Misty", 0, "m", (0, 0, 9, 9), False, False)]
    b = ScriptBrain([{"action": "clear_desktop"}, {"action": "done", "evidence": "minimised"},
                     {"action": "done", "evidence": "minimised"}])
    r = await loop.run("clear my desktop", hands=h, brain=b, home=tmp_path)
    assert r.outcome == "done"
    assert [c for c in h.calls if c[0] == "set_minimised"] == [("set_minimised", 501, 0, True)]
    assert "full screen (left as is): WhatsApp" in b.seen[1]["history"][0]


async def test_an_action_that_changed_nothing_twice_is_refused(tmp_path):
    h = FakeHands({"TextEdit": textedit()})
    h.menus = {501: {(): [MenuItem("Window", "", True)], ("Window",): [MenuItem("Minimize All", "", True)]}}
    again = {"action": "menu", "path": ["Window", "Minimize All"]}
    b = ScriptBrain([again, again, {"action": "look"}, again] + [{"action": "wait"}] * 10)
    r = await loop.run("minimise all", hands=h, brain=b, home=tmp_path)
    assert [c for c in h.calls if c[0] == "menu_press"].__len__() == 2      # the third try never ran
    assert "refused: tried 2 times" in b.seen[4]["history"][-1]
    assert r.outcome in ("stuck", "looped") and r.steps < 10


async def test_the_same_action_a_fourth_time_is_refused_even_if_it_changes_things(tmp_path, monkeypatch):
    h = FakeHands({"TextEdit": textedit()})
    monkeypatch.setattr(observe, "effect", lambda before, after: "Screen changed.")   # each search opened a new tab
    search = {"action": "shortcut", "keys": "cmd+t"}
    b = ScriptBrain([search, {"action": "wait"}] * 4 + [{"action": "done", "evidence": "x"}])
    await loop.run("find the record", hands=h, brain=b, home=tmp_path)
    assert sum("cmd+t" in str(c) or "'t'" in str(c) for c in h.calls) == 3
    assert any("going in circles" in line for line in b.seen[-1]["history"])


def test_titlebar_buttons_are_named_by_what_they_do():
    info, tree = textedit()
    tree.append(node("z1", "AXButton", "this button also has an action to zoom the window", subrole="AXZoomButton"))
    tree.append(node("z2", "AXButton", "", subrole="AXMinimizeButton"))
    text = observe.build(info, [FakeHands._snap(n) for n in tree]).render()
    assert '"full screen (green)"' in text and '"minimise window (yellow)"' in text


async def test_running_out_of_steps_after_opening_the_file_still_says_it_was_opened(tmp_path, monkeypatch):
    """V3, Isaac's live turn: open_path opened FUNCTIONS HL.docx, then six looks 'verified' a Finder window that never showed Word,
    the 20-step limit hit with an empty summary, and her reply said she had not found it."""
    from misty_engine.stream import actions
    f = tmp_path / "FUNCTIONS HL.docx"
    f.write_text("x")
    monkeypatch.setattr(actions.subprocess, "run", lambda *a, **k: None)         # never really open anything
    h = FakeHands({"TextEdit": textedit()})
    b = ScriptBrain([{"action": "open_path", "file": str(f)}] + [{"action": "look"}] * 8)
    r = await loop.run("open the functions worksheet", hands=h, brain=b, home=tmp_path, max_steps=6)
    assert r.outcome == "step_limit"
    assert r.did == ["opened FUNCTIONS HL.docx"]
    assert "FUNCTIONS HL.docx" in r.summary and "may have worked" in r.summary


async def test_a_run_that_did_nothing_keeps_an_empty_summary(tmp_path):
    h = FakeHands({"TextEdit": textedit()})
    b = ScriptBrain([{"action": "wait"}, {"action": "look"}, {"action": "wait"}, {"action": "look"}])
    r = await loop.run("x", hands=h, brain=b, home=tmp_path, max_steps=3)
    assert r.outcome == "step_limit" and r.did == [] and r.summary == ""
