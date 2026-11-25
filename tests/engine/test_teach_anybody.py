"""E18: the anybody test for the teacher. A made-up stranger (a gardener, then a nurse) on an empty data folder, a bread-starter source, and
the whole teach path with fake model replies: nothing about one person, school or exam board may appear in any reply, prompt or tool text.
Plus a code scan: the teach package never names his subjects, his school or an exam board. No paid call."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "checks"))
import anybody  # noqa: E402
import _harness  # noqa: E402

PKG = ROOT / "engine" / "src" / "misty_engine" / "teach"
# His name, his school and exam, and his subjects (SPEC law 1). Whole words, any case, in code, comments and docstrings alike.
NAMES = re.compile(r"\b(isaac|igcse|ib|cambridge|hougang|edexcel|aqa|cie|a-levels?|singapore|physics|chemistry|economics|spanish|"
                   r"english language|math aa|cricket)\b", re.I)


@pytest.fixture
def stranger(tmp_path, monkeypatch):
    made = []

    def make(persona):
        monkeypatch.setenv("MISTY_HOME", str(tmp_path / persona))
        ctx, home = anybody.setup(persona)
        made.append(home)
        return ctx
    yield make
    for h in made:
        _harness.cleanup(h)


@pytest.mark.parametrize("persona", ["retiree", "nurse"])
def test_the_teacher_works_for_a_stranger_with_no_name_in_anything_she_says(stranger, persona):
    ctx = stranger(persona)
    assert anybody.check_teach(ctx) == []


def test_the_run_really_taught_something(stranger):
    ctx = stranger("retiree")
    seen, out = [], []
    from misty_engine.teach import ask
    real = ask.think
    try:
        import asyncio
        asyncio.run(anybody._teach_run(ctx, seen, out))
    finally:
        ask.think = real
    q = lambda sql: [tuple(r) for r in ctx.con.execute(sql)]
    assert q("SELECT set_name, status FROM teach_probes ORDER BY at") == [("before", "done"), ("after", "done")]
    assert q("SELECT status FROM teach_courses") == [("active",)]
    assert q("SELECT status FROM teach_live") == [("done",)] and q("SELECT COUNT(*) FROM teach_drill WHERE kind='drill' AND status='done'")[0][0] == 1
    kinds = {r[0] for r in q("SELECT kind FROM teach_evidence")}
    assert {"right_alone", "right_helped", "wrong", "taught"} <= kinds           # right, wrong and stuck were all taken
    assert q("SELECT COUNT(*) FROM teach_slips")[0][0] >= 2
    assert len(seen) > 20 and len(out) > 20 and anybody.QUOTE in "".join(seen)


def test_a_name_in_a_prompt_is_caught(stranger, monkeypatch):
    ctx = stranger("retiree")
    from misty_engine.teach import plan
    monkeypatch.setattr(plan, "SYSTEM", plan.SYSTEM + "\nThe learner is Isaac, who takes the IB.")
    hits = anybody.check_teach(ctx)
    assert hits and any("Isaac" in h or "IB" in h for h in hits)


def test_the_code_names_none_of_his_subjects_his_school_or_an_exam_board():
    bad = []
    for f in sorted(PKG.glob("*.py")):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            m = NAMES.search(line)
            if m:
                bad.append(f"{f.name}:{n}: {m.group(0)}: {line.strip()[:70]}")
    assert not bad, "the teach package names a person's subject or school:\n" + "\n".join(bad)


def test_the_scan_sees_a_name():
    assert NAMES.search("quiz me in Spanish") and NAMES.search("the IB exam") and NAMES.search("Cambridge board")
    assert not NAMES.search("a limit, a mitre, a mark scheme") and not NAMES.search("the bib and the tib")
