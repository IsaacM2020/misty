from misty_engine.sources.mailclean import clean_mail


def test_clean_mail_strips_quotes_signatures_footers():
    raw = ("Thanks, see you Friday.\n\nOn Mon, 12 Oct 2026, Aarav wrote:\n> old stuff\n> more\n\n-- \nIsaac\n"
           "Sent from my iPhone")
    assert clean_mail(raw, is_html=False) == "Thanks, see you Friday."


def test_clean_mail_html_and_footer():
    html = "<html><style>x{}</style><body><p>Hi</p><p>Deadline moved.</p><p>Unsubscribe here</p></body></html>"
    assert clean_mail(html, is_html=True) == "Hi\nDeadline moved."


def test_outlook_header_block_and_original_message():
    raw = "Will do.\n\nFrom: Aarav\nSent: Monday\nTo: Isaac\nSubject: Re: x\n\nold text"
    assert clean_mail(raw, is_html=False) == "Will do."
    assert clean_mail("ok\n-----Original Message-----\nfoo", False) == "ok"


def test_from_line_in_normal_text_is_kept():
    assert "From: the lab" in clean_mail("Results came back.\nFrom: the lab we got 3 samples.", False)


def test_blank_lines_collapse_and_nbsp():
    assert clean_mail("a\n\n\n\nb\xa0c", False) == "a\nb c"
