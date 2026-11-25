import pytest

from misty_engine.address_guard import AddressTailGuard


def run(word, *chunks):
    g = AddressTailGuard(word)
    out = "".join(g.feed(c) for c in chunks)
    return out + g.flush()


def test_split_chunks_become_full_stop():
    assert run("boss", "Anytime", ",", " bo", "ss.") == "Anytime."
    assert run("boss", "It's 12:39 on Saturday, boss.") == "It's 12:39 on Saturday."


def test_no_final_punctuation_and_case():
    assert run("boss", "Sure, Boss") == "Sure."


def test_exclamation_kept():
    assert run("boss", "Done, boss!") == "Done!"
    assert run("boss", "Done, bo", "ss", "!") == "Done!"


def test_non_matching_released_unchanged():
    assert run("boss", "Take ", ", bo", "th of them") == "Take , both of them"
    assert run("boss", "Yes, bossy one.") == "Yes, bossy one."


def test_mid_sentence_and_start_left_alone():
    assert run("boss", "Yeah boss, got it, boss, and more.") == "Yeah boss, got it, boss, and more."
    assert run("boss", "Boss.") == "Boss."
    assert run("boss", ", boss.") == ", boss."


def test_no_address_word_is_noop():
    g = AddressTailGuard("")
    assert g.feed("Anytime, boss.") == "Anytime, boss."
    assert g.flush() == ""


def test_delay_is_small():
    g = AddressTailGuard("boss")
    assert g.feed("Hello there, ") == "Hello there"
    assert g.feed("friend") == ", friend"


def strip(*chunks):
    g = AddressTailGuard("boss", allowed=False)
    return "".join(g.feed(c) for c in chunks) + g.flush()


def test_not_allowed_removes_the_word_everywhere():
    assert strip("Not much, boss.") == "Not much."
    assert strip("Happy to, boss. What now?") == "Happy to. What now?"
    assert strip("Boss, I don't have that.") == "I don't have that."
    assert strip("Boss! yes it is") == "Yes it is"
    assert strip("I'd be guessing, boss") == "I'd be guessing."
    assert strip("Okay, boss, sure thing.") == "Okay, sure thing."
    assert strip("Yes, I can see it, boss!") == "Yes, I can see it!"


def test_not_allowed_odd_chunk_splits():
    assert strip("B", "os", "s", ",", " I", " can't") == "I can't"
    assert strip("Not much", ",", " bo", "ss", ".") == "Not much."
    assert strip("Happy to", ", b", "oss", ". ", "What") == "Happy to. What"
    assert strip("Okay", ",", " boss", ",", " sure") == "Okay, sure"
    assert strip("Okay, bo", "ssy man, ", "boss") == "Okay, bossy man."


def test_not_allowed_leaves_other_words_and_a_lone_vocative():
    assert strip("Bossa nova is fun, both ways.") == "Bossa nova is fun, both ways."
    assert strip("The boss at work, bossy.") == "The boss at work, bossy."
    assert strip("Boss.") == "Boss."


def test_not_allowed_holds_only_a_few_characters():
    g = AddressTailGuard("boss", allowed=False)
    assert g.feed("Sure thing and then some, ") == "Sure thing and then some"
    assert g.feed("really") == ", really"


def test_allowed_only_when_none_of_the_last_three_replies_used_it():
    import sqlite3
    from misty_engine.address_guard import address_allowed
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, role TEXT, text TEXT)")
    for role, text in [("assistant", "Yeah, Boss, sure."), ("user", "boss?"), ("assistant", "ok"), ("assistant", "fine"), ("assistant", "done")]:
        con.execute("INSERT INTO chat_messages(role,text) VALUES (?,?)", (role, text))
    assert address_allowed(con, "boss") is True                  # the last 3 assistant replies are clean (the user's own word does not count)
    con.execute("INSERT INTO chat_messages(role,text) VALUES ('assistant','Okay, BOSS.')")
    assert address_allowed(con, "boss") is False
    for t in ("a", "b", "c"):
        con.execute("INSERT INTO chat_messages(role,text) VALUES ('assistant',?)", (t,))
    assert address_allowed(con, "boss") is True
    assert address_allowed(con, "") is True


def test_not_allowed_interjection_keeps_the_interjection_and_drops_the_word():
    assert strip("Yeah boss, want me to open it?") == "Yeah, want me to open it?"
    assert strip("Okay boss, sure.") == "Okay, sure."
    assert strip("Sure boss.") == "Sure."
    assert strip("Thanks boss!") == "Thanks!"
    assert strip("Hey Boss, what's up") == "Hey, what's up"
    assert strip("Got it boss, on it") == "Got it, on it"
    assert strip("Yeah boss want me to?") == "Yeah want me to?"
    assert strip("Sure boss") == "Sure"


def test_interjection_strip_survives_odd_chunk_splits():
    text = "Yeah boss, want me to open it?"
    want = "Yeah, want me to open it?"
    for n in (1, 2, 3, 5):
        assert strip(*[text[i:i + n] for i in range(0, len(text), n)]) == want
    assert strip("Ye", "ah", " ", "bo", "ss", ".") == "Yeah."
    assert strip("Got", " it", " boss", "!") == "Got it!"
    assert strip("Sure bo", "ss", ", ok") == "Sure, ok"


def test_interjection_strip_leaves_other_text_alone_and_holds_little():
    assert strip("Yeah bossy one, sure") == "Yeah bossy one, sure"
    assert strip("Yeah, I think so") == "Yeah, I think so"
    assert strip("Right now it is 9") == "Right now it is 9"
    assert strip("Okay then, boss.") == "Okay then."
    assert strip("Nice work, thanks boss, bye") == "Nice work, thanks boss, bye"      # only at the start of the reply
    g = AddressTailGuard("boss", allowed=False)
    assert g.feed("Yeah, ") == "Yeah"
    assert g.feed("go on") == ", go on"
    g = AddressTailGuard("boss", allowed=True)
    assert g.feed("Yeah boss, ok") == "Yeah boss, ok"          # allowed: only a trailing ", boss" is touched
