"""Words handed to Whisper before a class, and his `heard => wanted` fixes after it: a bad dictionary file must never
stop the after-class pass (it costs ten minutes of reading to find out at the end)."""
from misty_engine import subject_words


def test_prompt_has_his_words_first_then_the_subjects(tmp_path):
    (tmp_path / "dictionary.txt").write_text("# mine\ndoppler => Doppler\nkay see => Kc\n", encoding="utf-8")
    p = subject_words.prompt_for("Physics", tmp_path)
    assert p.startswith("A Physics class. Doppler, Kc, SI units")
    assert len(p) <= subject_words.MAX_CHARS
    assert subject_words.prompt_for("History", tmp_path) == "A History class. Doppler, Kc"
    assert subject_words.prompt_for("History", tmp_path / "none") is None


def test_dictionary_fixes_whole_words_any_case(tmp_path):
    (tmp_path / "dictionary.txt").write_text("kay see => Kc\n", encoding="utf-8")
    assert subject_words.apply_dictionary(tmp_path, "the Kay See value, not okay seeing") == "the Kc value, not okay seeing"


def test_a_dictionary_that_is_not_utf8_is_ignored_not_fatal(tmp_path):
    (tmp_path / "dictionary.txt").write_bytes("doppler => Dopplér\n".encode("utf-16"))       # saved as UTF-16 by an editor
    assert subject_words.wanted_words(tmp_path) == []
    assert subject_words.prompt_for("Chem", tmp_path).startswith("A Chem class. mole")
    assert subject_words.apply_dictionary(tmp_path, "the doppler effect") == "the doppler effect"
