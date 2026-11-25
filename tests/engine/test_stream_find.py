"""V5: find a file by name in one step (Spotlight), and stop once the file is open. Fakes only, nothing is opened for real."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fakes_hands import FakeHands, textedit
from misty_engine.stream import actions, loop
from misty_engine.stream.bridge import AppInfo
from test_stream_core import ScriptBrain

class Ran:
    stdout = ""
    returncode = 0


RUN = json.loads((Path(__file__).parent / "fixtures" / "worksheet_run.json").read_text())


def two_apps():
    fi, ft = textedit()
    finder = (AppInfo("com.apple.finder", 100, "Finder"), ft)
    word = (AppInfo("com.microsoft.Word", 200, "Microsoft Word"), list(ft))
    return {"Finder": finder, "Microsoft Word": word}


def churn(h):
    """Every screen read differs a little, like a live window, so no step counts as 'nothing changed'."""
    from fakes_hands import node
    n = [0]

    def hook(kind, *args):
        if kind == "ax_snapshot":
            n[0] += 1
            for _, tree in h.apps.values():
                tree[0] = node("a1", "AXWindow", f"Window {n[0]}", actions=(), depth=0)
    h.on_action = hook


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    home = tmp_path / "home"
    d = home / "Downloads"
    d.mkdir(parents=True)
    old = d / "functions notes old.pdf"
    new = d / "FUNCTIONS HL.docx"
    other = d / "algebra.pdf"
    for i, f in enumerate((old, new, other)):
        f.write_text("x")
        import os
        os.utime(f, (1_000_000 + i * 100, 1_000_000 + i * 100))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))

    def fake_mdfind(folder, word):
        return [str(p) for p in Path(folder).rglob("*") if word.lower() in p.name.lower()]
    monkeypatch.setattr(actions, "_mdfind", fake_mdfind)
    return d


def test_find_files_returns_matches_newest_first(downloads):
    out = actions.find_files("Downloads", "functions")
    lines = out.splitlines()
    assert "2 files" in lines[0] and "functions" in lines[0]
    assert lines[1].endswith("FUNCTIONS HL.docx") and lines[2].endswith("functions notes old.pdf")


def test_every_word_must_match(downloads):
    assert "FUNCTIONS HL.docx" in actions.find_files("Downloads", "functions hl")
    assert "no file" in actions.find_files("Downloads", "functions zzz")


def test_at_most_ten_paths(downloads):
    for i in range(14):
        (downloads / f"functions {i}.pdf").write_text("x")
    lines = actions.find_files("Downloads", "functions").splitlines()
    assert len(lines) == 1 + 10 and "10 of 16" in lines[0]


def test_outside_home_and_private_places_are_refused(downloads, tmp_path):
    assert "refused" in actions.find_files("/etc", "passwd")
    assert "refused" in actions.find_files("~/Library", "x")
    assert "refused" in actions.find_files("~/.ssh", "id_rsa")
    assert "refused" in actions.find_files("Downloads/../..", "x")
    (downloads / ".hidden functions.txt").write_text("x")
    assert ".hidden" not in actions.find_files("Downloads", "functions")


def test_empty_name_is_not_a_search(downloads):
    assert "needs" in actions.find_files("Downloads", "  ")


def test_find_files_is_a_real_action_with_its_prompt_line():
    from misty_engine.stream.brain import SYSTEM
    assert "find_files" in actions.ACTIONS
    assert "find_files" in SYSTEM and "search box" in SYSTEM


@pytest.mark.parametrize("task,only", [
    (RUN["task"], True),
    ("open the functions worksheet", True),
    ("find the maths worksheet in Downloads and open it", True),
    ("open the worksheet and email it to my teacher", False),
    ("open my essay and write a conclusion", False),
    ("open the pdf then summarise page 2", False),
    ("open the worksheet and read me the first question", False),
])
def test_only_open_is_judged_from_the_task(task, only):
    assert loop.only_open(task) is only


async def test_the_recorded_run_now_stops_at_the_open_not_at_the_step_limit(tmp_path, monkeypatch, downloads):
    """The real run: 13 steps hunting in Finder, open at step 14, six wasted looks, step limit, empty summary."""
    h = FakeHands(two_apps(), front="Finder")
    churn(h)
    calls = []

    def fake_run(args, *a, **k):
        calls.append(args)
        if args and args[0] == "open":
            h.front = "Microsoft Word"          # Word came to the front
        return Ran()
    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    acts = [dict(a) for a in RUN["actions"][:14]] + [{"action": "look"}] * 6
    acts[13]["file"] = str(downloads / "FUNCTIONS HL.docx")
    acts = [a if a.get("action") != "open_path" or a.get("file", "").endswith(".docx") else {"action": "wait"} for a in acts]
    b = ScriptBrain(acts)
    r = await loop.run(RUN["task"], hands=h, brain=b, home=tmp_path, max_steps=20)
    assert r.outcome == "done" and r.steps == 14, (r.outcome, r.steps)
    assert "FUNCTIONS HL.docx" in r.summary and "Microsoft Word" in r.summary


async def test_the_same_ask_takes_three_steps_with_find_files(tmp_path, monkeypatch, downloads):
    h = FakeHands(two_apps(), front="Finder")

    def fake_run(args, *a, **k):
        if args and args[0] == "open" and args[-1].endswith(".docx"):
            h.front = "Microsoft Word"
        return Ran()
    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    b = ScriptBrain([{"action": "find_files", "folder": "Downloads", "text": "functions"},
                     {"action": "open_path", "file": str(downloads / "FUNCTIONS HL.docx")}])
    r = await loop.run(RUN["task"], hands=h, brain=b, home=tmp_path, max_steps=20)
    assert r.outcome == "done" and r.steps <= 4, (r.outcome, r.steps)
    assert "FUNCTIONS HL.docx" in r.summary
    assert "FUNCTIONS HL.docx" in b.seen[1]["history"][0]       # the brain saw the paths after step 1


async def test_a_task_with_more_to_do_does_not_stop_after_the_open(tmp_path, monkeypatch, downloads):
    h = FakeHands(two_apps(), front="Finder")

    def fake_run(args, *a, **k):
        if args and args[0] == "open":
            h.front = "Microsoft Word"
        return Ran()
    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    b = ScriptBrain([{"action": "open_path", "file": str(downloads / "FUNCTIONS HL.docx")}, {"action": "look"}, {"action": "done", "evidence": "x"}])
    r = await loop.run("open the worksheet and email it to my teacher", hands=h, brain=b, home=tmp_path, max_steps=5)
    assert r.steps == 3


async def test_if_the_front_app_never_changes_the_run_does_not_claim_done(tmp_path, monkeypatch, downloads):
    h = FakeHands(two_apps(), front="Finder")
    churn(h)
    monkeypatch.setattr(actions.subprocess, "run", lambda *a, **k: Ran())     # nothing came to the front
    monkeypatch.setattr(actions, "FRONT_WAIT", 0.0)
    b = ScriptBrain([{"action": "open_path", "file": str(downloads / "FUNCTIONS HL.docx")}, {"action": "done", "evidence": "opened"}])
    r = await loop.run("open the functions worksheet", hands=h, brain=b, home=tmp_path, max_steps=5)
    assert r.steps == 2                                                       # the brain still decides
