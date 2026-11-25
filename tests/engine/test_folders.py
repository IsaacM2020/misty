import os

from misty_engine.sources.folders import FolderSource
from misty_engine.sources.repos import repo_summary_text


def test_walk_finds_supported_and_secret_files(tree):
    items = list(FolderSource([tree / "Desktop"], [], include_images=True).discover(None))
    kinds = {i.title: i.kind for i in items}
    assert kinds["essay.pdf"] == "file" and kinds["photo.heic"] == "image" and kinds[".env"] == "secret_file"
    assert "script.py" not in kinds                      # code is never extracted


def test_walk_never_enters_excluded(tree):
    ex = [tree / "Code/Water/oldapp", tree / "Code/_archive", tree / "Old Knowledge Base"]
    items = list(FolderSource([tree / "Code", tree], ex).discover(None))
    assert items
    assert not any("/evie/" in i.locator or "_archive" in i.locator or "Sentient OS" in i.locator for i in items)


def test_nested_repo_is_one_item_root_repo_is_walked(tree):
    items = list(FolderSource([tree / "Notes", tree / "Code"], []).discover(None))
    assert any(i.title == "notes.md" and "/Notes/" in i.locator for i in items)
    repos = [i for i in items if i.kind == "repo"]
    assert [r.title for r in repos] == ["tool"]
    assert not any("/Water/tool/" in i.locator and i.kind != "repo" for i in items)


def test_overlapping_roots_do_not_duplicate(tree):
    items = list(FolderSource([tree / "Notes", tree], []).discover(None))
    locs = [i.locator for i in items]
    assert len(locs) == len(set(locs))
    assert not any(i.kind == "repo" and i.title == "Notes" for i in items)


def test_walk_skips_dataless_and_icloud_stubs(tree, monkeypatch):      # Review Focus 3
    from misty_engine.sources import folders
    real = folders.file_flags
    monkeypatch.setattr(folders, "file_flags",
                        lambda p: folders.SF_DATALESS if p.name == "cloud-only.pdf" else real(p))
    src = FolderSource([tree / "Desktop"], [])
    titles = [i.title for i in src.discover(None)]
    assert "cloud-only.pdf" not in titles and ".old.pdf.icloud" not in titles
    assert src.stats["dataless"] == 2


def test_symlinks_are_not_followed(tree):
    os.symlink(tree / "Notes", tree / "Desktop/linked-dir")
    os.symlink(tree / "Notes/notes.md", tree / "Desktop/linked.md")
    titles = [i.title for i in FolderSource([tree / "Desktop"], []).discover(None)]
    assert "linked.md" not in titles and "notes.md" not in titles


def test_unreadable_dir_is_counted_not_fatal(tree):
    locked = tree / "Desktop/locked"
    locked.mkdir()
    (locked / "a.md").write_text("x")
    os.chmod(locked, 0)
    try:
        src = FolderSource([tree / "Desktop"], [])
        assert list(src.discover(None))
        assert src.stats["no_access"] == 1
    finally:
        os.chmod(locked, 0o700)


def test_limit(tree):
    assert len(list(FolderSource([tree], []).discover(None, limit=2))) == 2


def test_secret_file_load_text_is_name_only(tree):
    src = FolderSource([tree / "Desktop"], [])
    env = next(i for i in src.discover(None) if i.title == ".env")
    assert src.load_text(env) == ".env"


def test_repo_summary_reads_readme_and_tree_only(tree):
    s = repo_summary_text(tree / "Code/Water/tool")
    assert "Tool README" in s and "src/" in s and "def secret_impl" not in s
    assert "node_modules" not in s


def test_images_are_off_by_default(tree):          # decision P26
    src = FolderSource([tree / "Desktop"], [])
    kinds = {i.title: i.kind for i in src.discover(None)}
    assert "photo.heic" not in kinds and kinds["essay.pdf"] == "file"
    assert src.stats["images_deferred"] == 1
