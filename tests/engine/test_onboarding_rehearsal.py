"""A stranger's onboarding, end to end: compile (FakeOpenRouter), resolve, judge and summaries (ScriptedRunner), pages.
No real persona, no paid call, and nothing in the engine may start a `claude` process."""
import re
import subprocess

import pytest

import personas as P
from misty_engine.build import Build
from misty_engine.registry import render
from misty_engine.registry.store import Registry

NAMES = ("nurse", "founder", "student", "retiree")


def _drafts(fake_or, seed, fctx, name):
    notes, ents, _, _, _ = P.corpus(name)
    kind_of = {e.path: e.kind for e in ents}
    title_of = {e.path: e.title for e in ents}
    variant: dict[str, str] = {}
    for e in ents:
        if e.kind == "person" and " " in e.title:                      # "Sister Okafor" is also written "Okafor"
            variant[e.path] = e.title.split()[-1]
    ids = []
    for n in notes:
        seed(n.item_id, f"{n.title}. {n.summary} " * 6, source={"calendar": "gcal", "files": "folders"}.get(
            n.source, n.source), kind={"gmail": "mail_thread", "gcal": "calendar_week", "calendar": "calendar_week",
                                       "imessage": "chat_week"}.get(n.source, "file"), title=n.title)
        people, projects, topics = [], [], []
        for k, path in enumerate(n.entities):
            nm = title_of[path]
            if kind_of[path] == "person":
                people.append(variant.get(path, nm) if k % 2 else nm)    # alternate spellings across notes
            elif kind_of[path] == "project":
                projects.append(nm)
            else:
                topics.append(nm)
        fake_or.set_draft(n.item_id, title=n.title, summary=n.summary, people=people, projects=projects,
                          topics=topics, dates=[{"date": n.date, "line": n.title}])
        fctx.con.execute("UPDATE items SET status='triaged', matters=1 WHERE id=?", (n.item_id,))
        ids.append(n.item_id)
    return ids, variant, title_of


@pytest.mark.asyncio
@pytest.mark.parametrize("name", NAMES)
async def test_a_stranger_onboards_clean(fctx, fake_or, seed, name, monkeypatch):
    import re as _re
    from fakes import ScriptedRunner
    spawned = []
    real_run = subprocess.run

    def watch(cmd, *a, **k):
        spawned.append(cmd if isinstance(cmd, str) else " ".join(map(str, cmd)))
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", watch)

    ids, variant, title_of = _drafts(fake_or, seed, fctx, name)

    def judge(prompt):
        n = len(_re.findall(r"^\d+\.$", prompt, _re.M))
        return {"pairs": [{"id": i, "verdict": "same", "why": "same name"} for i in range(1, n + 1)]}

    def summaries(prompt):
        return {"entities": [{"id": i, "summary": "A real summary sentence about this.", "now": [],
                              "relationship": "other", "role": ""} for i in _re.findall(r"^id: (en_[0-9a-f]+)$",
                                                                                       prompt, _re.M)]}
    fctx.runner = ScriptedRunner()
    fctx.runner.set("judge", judge)
    fctx.runner.set("summaries", summaries)
    fctx.sources = {}
    await Build(fctx).run()

    lint = render.lint(fctx)
    assert all(v == 0 for k, v in lint.items() if k != "orphan_files"), lint
    reg = Registry(fctx.con, fctx.clock)
    # every active page has a real summary and a file, and is not a stub
    rows = fctx.con.execute("SELECT e.id, e.path, t.summary FROM entities e LEFT JOIN entity_text t ON t.entity_id = e.id "
                            "WHERE e.status='active'").fetchall()
    assert rows, "a stranger should get pages"
    for r in rows:
        assert (r["summary"] or "").strip() not in ("", "(stub)") and fctx.wiki.exists(r["path"])
    # one entity per person even though some notes wrote the short spelling
    for path, short in variant.items():
        full = title_of[path]
        ents = {reg.survivor(x) for key in (full, short) for x in reg.find_alias("person", " ".join(
            re.sub(r"[^a-z0-9 ]", " ", key.lower()).split()))}
        assert len(ents) <= 1, f"{full} / {short} ended as {len(ents)} entities"
    assert not any(re.search(r"(^|[\s/])claude(\s|$)", s) for s in spawned), spawned
    assert fctx.queue.counts().get("failed", 0) == 0
