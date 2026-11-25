import pytest

from misty_engine.compile.haiku import NoteDraft
from misty_engine.wiki.notes import (ENTITY_SECTIONS, dump_page, links_in, parse_page, render_source_note,
                                     slugify, source_note_path, validate_page)

HOSTILE = ['iGEM: "2026" — team #1\nline2 \U0001F3CF', "- leading dash", "null", "true", "1e3", "[x]", "---",
           "a: b: c", "Müller & Søren’s Café", "日本語のタイトル",
           "'single'", "tab\there", "  padded  ", "x" * 500]


def draft(title="T", **kw):
    d = dict(item_id="it_1", title=title, summary="Summary line.", key_facts=("a fact",), people=("Aarav Shah",),
             projects=("iGEM",), topics=("wiki",))
    d.update(kw)
    return NoteDraft(**d)


@pytest.mark.parametrize("title", HOSTILE)
def test_note_roundtrip_hostile_titles(title):                       # Review Focus 5
    fm, body = render_source_note(draft(title=title), item_id="it_00000000000a1b2c", source="gmail",
                                  today="2026-10-14")
    fm2, body2 = parse_page(dump_page(fm, body))
    assert fm2["title"] == " ".join(title.split()) and body2.strip() == body.strip()
    assert validate_page(fm2, body2) == []


def test_slugify_unique_and_ascii():
    a = source_note_path("gmail", "it_00000000000a1b2c", "日本語")
    b = source_note_path("gmail", "it_00000000000a1b2d", "日本語")
    assert a != b and a.isascii() and "/" not in a.split("/", 2)[2]
    assert slugify("Müller & Søren’s Café") == "muller-soren-s-cafe"
    assert slugify("\U0001F3CF\U0001F525") == "untitled" and len(slugify("x" * 500)) <= 60


def test_source_note_links_people_projects_topics():
    fm, body = render_source_note(draft(), item_id="it_1", source="gmail", today="2026-10-14")
    assert set(links_in(body)) == {"people/aarav-shah", "projects/igem", "topics/wiki"}


FM = {"type": "project", "title": "p", "updated": "2026-10-14"}
ENTITY_BODY = "## Summary\ns\n\n## Now\n- a\n\n## Timeline\n- 2026-10-14: x\n\n## Links\n"


def test_validate_ok_and_required_sections_in_order():
    assert validate_page(FM, ENTITY_BODY) == []
    body = "## Summary\ns\n\n## Timeline\n- 2026-10-14: x\n\n## Now\n- a\n\n## Links\n"
    assert "SECTIONS_ORDER" in validate_page(FM, body)
    assert "SECTIONS_MISSING" in validate_page(FM, "## Summary\ns\n")
    assert "FM_KEYS" in validate_page({"type": "project"}, ENTITY_BODY)


def test_validate_timeline_and_now_limits():
    assert "NOW_TOO_LONG" in validate_page(FM, ENTITY_BODY.replace("- a", "\n".join(f"- {i}" for i in range(6))))
    assert "TIMELINE_FORMAT" in validate_page(FM, ENTITY_BODY.replace("2026-10-14: x", "yesterday x"))


def test_parse_page_rejects_missing_frontmatter():
    from misty_engine.wiki.notes import NoteError
    with pytest.raises(NoteError):
        parse_page("no frontmatter here")


def test_entity_sections_constant():
    assert ENTITY_SECTIONS == ("Summary", "Now", "Timeline", "Links")
