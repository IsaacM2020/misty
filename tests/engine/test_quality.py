from misty_engine import quality


def _page(root, rel, title, typ="person", summary="(stub)", sources=0, body_extra=""):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    src = "[" + ", ".join(f"it_{i}" for i in range(sources)) + "]"
    p.write_text(f"---\ntype: {typ}\ntitle: {title}\naliases: []\nsources: {src}\nupdated: 2026-10-15\n---\n\n"
                 f"## Summary\n{summary}\n\n## Now\n\n## Timeline\n\n## Links\n{body_extra}\n")


def test_report_counts(tmp_path, con):
    w = tmp_path / "wiki"
    _page(w, "people/pranav.md", "Pranav", sources=3)
    _page(w, "people/pranav-asrani.md", "Pranav Asrani", sources=5, summary="Friend from school.")
    _page(w, "people/mc-sa2-over-chat-member.md", "MC SA2 OVER chat member", sources=1)
    _page(w, "topics/x.md", "X", typ="topic", sources=2, summary="s", body_extra="- [[people/ghost]]")
    r = quality.report(w, con)
    assert r["dup_pairs_subset"] == 1
    assert r["stub_pages_2plus_sources"] == 1          # pranav.md
    assert r["chat_name_people"] == 1
    assert r["dead_links"] == 1
    assert r["entity_pages"] == 4


def test_nested_source_links_are_not_dead(tmp_path, con):
    w = tmp_path / "wiki"
    (w / "sources/whatsapp").mkdir(parents=True)
    (w / "sources/whatsapp/note-1.md").write_text("---\ntype: source\n---\nbody\n")
    _page(w, "people/a.md", "A", sources=2, summary="s",
          body_extra="- [[sources/whatsapp/note-1]]\n- [[sources/whatsapp/missing]]")
    assert quality.report(w, con)["dead_links"] == 1


def test_private_leaks_counts_pages_citing_a_private_chat(tmp_path, con):
    import json
    w = tmp_path / "wiki"
    _page(w, "people/a.md", "A", sources=0)
    con.execute("INSERT INTO items(id, source, kind, locator, title, status, note_path, meta, updated_at) VALUES "
                "('it_0','whatsapp','chat_week','x','t','done','sources/whatsapp/n0.md',?,0)",
                (json.dumps({"chat_id": "cp"}),))
    _page(w, "people/leak.md", "Leak", summary="s", sources=0, body_extra="- [[sources/whatsapp/n0]]")
    assert quality.report(w, con)["private_leaks"] == 0                       # nothing private configured
    con.execute("INSERT INTO settings(key, value) VALUES ('context_private_chats', ?)",
                (json.dumps(["whatsapp:cp"]),))
    assert quality.report(w, con)["private_leaks"] == 1
