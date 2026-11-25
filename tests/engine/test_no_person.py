"""Law 1: nothing about one person in the code (card B4). Her prompts, tool descriptions and the lines she sends to a model
hold tokens (<<name>>, <<about>>, <<subjects>>) that `persona.render` fills from settings.

The check reads every string literal in the finished files (not docstrings, not comments: those talk to a developer, not to a
model) and fails on his name or his subjects. The files NOT yet done sit in NOT_DONE below, copied from SPEC section 8, so the
debt stays visible. When one is finished, move it into DONE.
"""
import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "engine" / "src" / "misty_engine"
DONE = ["chat.py", "brief.py", "follows.py", "lessons.py", "feedback.py", "gate.py", "cares.py", "ways.py", "persona.py",
        "wrong_picks.py"]
# Known debt (SPEC section 8, found 2026-11-10; not step B's job):
NOT_DONE = [
    "tutor.py",                    # the solve prompt and the lesson board still say "Isaac" (found 2026-11-15, not in the B4 list)
    "compile/", "wiki/", "prep.py", "class_notes.py", "registry/", "stream/",    # the memory pipeline and Stream prompts
    "config/bootstrap-about-isaac.md",                                          # the bootstrap card
    # Singapore time (sgt_date, quiet hours) is assumed everywhere; bundle id com.isaacmiranda.Misty; the wake names in VoiceVM.route.
]
FORBIDDEN = re.compile(r"\bIsaac\b|\bIGCSE\b|\bIB\b|Chem, Math, Physics")


def strings(path: Path):
    tree = ast.parse(path.read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) and isinstance(b[0].value.value, str):
                docs.add(id(b[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def test_no_person_in_the_finished_files():
    bad = []
    for name in DONE:
        for line, text in strings(SRC / name):
            if FORBIDDEN.search(text):
                bad.append(f"{name}:{line}: {text[:70]!r}")
    assert not bad, "a person's name or subject is back in a prompt:\n" + "\n".join(bad)


def test_the_guard_actually_sees_a_name(tmp_path):
    f = tmp_path / "x.py"
    f.write_text('"""Isaac in a docstring is fine."""\nX = "tell Isaac"  # Isaac in a comment is fine\n')
    assert [t for _, t in strings(f)] == ["tell Isaac"]
    assert FORBIDDEN.search("tell Isaac")


def test_every_not_done_file_still_exists_so_the_list_stays_honest():
    for item in NOT_DONE:
        if item.endswith((".py", "/")) and not item.startswith("config"):
            assert (SRC / item.rstrip("/")).exists(), item
