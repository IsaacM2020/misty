import subprocess

import pytest

from misty_engine.compile.haiku import NoteDraft
from misty_engine.errors import Fatal
from misty_engine.wiki.notes import render_source_note
from misty_engine.wiki.store import WikiStore


def commits(root):
    return int(subprocess.run(["git", "-C", str(root), "rev-list", "--count", "HEAD"],
                              capture_output=True, text=True).stdout)


def render(n):
    d = NoteDraft(item_id=f"it_{n}", title=f"Note {n}", summary="s", key_facts=("f",))
    return render_source_note(d, item_id=f"it_{n:016d}", source="gmail", today="2026-10-14")


@pytest.fixture
def store(tmp_path, clock):
    s = WikiStore(tmp_path / "wiki", clock)
    s.init()
    return s


def test_init_creates_layout_and_is_idempotent(store, tmp_path):
    root = tmp_path / "wiki"
    for d in ("people", "projects", "areas", "topics", "sources", "answers"):
        assert (root / d).is_dir()
    for f in ("index.md", "log.md", "corrections.md", "about-isaac.md", "priorities.md"):
        assert (root / f).is_file()
    n = commits(root)
    store.init()
    assert commits(root) == n


def test_store_batch_commits_once_and_logs(store, tmp_path):
    root = tmp_path / "wiki"
    before = commits(root)
    with store.batch("compile: 2 source notes"):
        store.write("sources/gmail/a-000001.md", *render(1))
        store.write("sources/gmail/b-000002.md", *render(2))
    assert commits(root) == before + 1
    assert "compile: 2 source notes" in (root / "log.md").read_text()
    assert store.read("sources/gmail/a-000001.md").fm["title"] == "Note 1"


def test_write_outside_batch_commits_immediately(store, tmp_path):
    before = commits(tmp_path / "wiki")
    store.write("sources/gmail/a-000001.md", *render(1))
    assert commits(tmp_path / "wiki") == before + 1


def test_ensure_stub_and_resolve_links(store, tmp_path):
    created = store.resolve_links("see [[people/aarav-shah]] and [[topics/igem]]")
    assert (tmp_path / "wiki/people/aarav-shah.md").exists() and len(created) == 2
    page = store.read("people/aarav-shah.md")
    assert page.fm["type"] == "person" and "(stub)" in page.body
    assert store.resolve_links("[[people/aarav-shah]]") == []


def test_rejects_invalid_page_and_path_escape(store):
    with pytest.raises(Fatal):
        store.write("projects/x.md", {"type": "project", "title": "x", "updated": "d"}, "no sections")
    with pytest.raises(Fatal):
        store.write("../evil.md", *render(1))


def test_revert_undoes_commit(store, tmp_path):
    root = tmp_path / "wiki"
    with store.batch("add"):
        store.write("sources/gmail/a-000001.md", *render(1))
    head = store.head()
    store.revert(head)
    assert not (root / "sources/gmail/a-000001.md").exists()
    assert subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True,
                          text=True).stdout.strip() == ""
    assert commits(root) == 3          # init, batch, revert (history is kept)
