"""The free local mail pre-filter (P33): metadata only, junk skipped, allowlist and replies always kept."""
import pytest

from fakes_google import make_thread
from misty_engine.sources.mailfilter import classify, domain_allowed, is_automated_address

ME = "you@example.com"


def v(**kw):
    return classify(make_thread("t", **kw), ME)


def test_plain_human_mail_is_kept():
    assert (v().keep, v().rule) == (True, "human")


@pytest.mark.parametrize("label,rule", [("CATEGORY_PROMOTIONS", "promotions"), ("CATEGORY_SOCIAL", "social")])
def test_promotions_and_social_are_skipped(label, rule):
    r = v(labels=("INBOX", label))
    assert (r.keep, r.rule) == (False, rule)


def test_updates_category_from_a_human_is_kept():
    assert v(labels=("INBOX", "CATEGORY_UPDATES")).keep


@pytest.mark.parametrize("sender", [
    "App <noreply@app.com>", "Bank <no-reply@bank.com>", "x <do-not-reply@x.com>", "x <donotreply@x.com>",
    "GitHub <notifications@github.com>", "x <mailer-daemon@googlemail.com>", "x <no_reply+abc@x.com>",
    "x <alerts@x.com>", "x <newsletter@x.com>", "x <bounce-123@x.com>"])
def test_noreply_and_notification_senders_are_skipped(sender):
    r = v(sender=sender)
    assert (r.keep, r.rule) == (False, "automated_sender")


def test_bulk_headers_are_skipped():
    assert v(headers={"List-Unsubscribe": "<mailto:u@x.com>"}).rule == "bulk_unsubscribe"
    assert v(headers={"Precedence": "bulk"}).rule == "bulk_precedence"
    assert v(headers={"Auto-Submitted": "auto-generated"}).rule == "auto_submitted"
    assert v(headers={"Auto-Submitted": "no"}).keep


def test_classroom_allowlist_beats_noreply_and_bulk():
    r = v(sender="Google Classroom <no-reply@classroom.google.com>", labels=("INBOX", "CATEGORY_UPDATES"),
          headers={"List-Unsubscribe": "<https://classroom.google.com/unsub>"})
    assert (r.keep, r.rule) == (True, "allowlist")


def test_allowlist_matches_subdomains_but_not_lookalikes():
    assert domain_allowed("a@mail.classroom.google.com", ["classroom.google.com"])
    assert not domain_allowed("a@evilclassroom.google.com.example.com", ["classroom.google.com"])
    assert not domain_allowed("a@notclassroom.google.com", ["classroom.google.com"])


def test_school_domain_from_settings_is_kept():
    t = make_thread("s", sender="Mr Tan <noreply@school.edu.sg>", headers={"List-Unsubscribe": "<x>"})
    assert not classify(t, ME).keep
    assert classify(t, ME, allow_domains=["school.edu.sg"]).rule == "allowlist"


def test_a_thread_i_replied_to_is_kept_even_when_it_is_bulk_or_promo():
    r = v(sender="Shop <deals@shop.com>", labels=("CATEGORY_PROMOTIONS",),
          headers={"List-Unsubscribe": "<x>"}, reply_from=(ME, ("SENT",)))
    assert (r.keep, r.rule) == (True, "replied")


def test_sent_label_alone_counts_as_replied():
    assert v(sender="x <noreply@x.com>", labels=("SENT",)).rule == "replied"


def test_starred_is_kept():
    r = v(sender="Shop <deals@shop.com>", labels=("INBOX", "CATEGORY_PROMOTIONS", "STARRED"))
    assert (r.keep, r.rule) == (True, "starred")


def test_human_in_a_noreply_thread_keeps_it():
    r = v(sender="App <noreply@app.com>", reply_from=("Priya <priya@example.com>", ("INBOX",)))
    assert r.keep


def test_automated_address_matcher_does_not_catch_people():
    assert not is_automated_address("notifier.fan@example.com")      # "notifier" is not "notify"/"notification"
    assert not is_automated_address("replyan@example.com")
    assert is_automated_address("notifications@x.com")


def test_mail_i_sent_only_to_myself_is_not_a_reply():
    r = v(sender=f"Immich <{ME}>", labels=("SENT", "INBOX"), headers={"To": ME})
    assert (r.keep, r.rule) == (True, "human")          # not "replied": the normal rules decide (and Jev later)
    assert v(sender=f"Immich <{ME}>", labels=("SENT",), headers={"To": ME, "List-Unsubscribe": "<x>"}).rule == \
        "bulk_unsubscribe"


def test_mail_i_sent_to_someone_is_a_reply_even_if_they_never_answered():
    r = v(sender=f"Isaac <{ME}>", labels=("SENT",), headers={"To": "Mr Tan <tan@school.edu.sg>"})
    assert (r.keep, r.rule) == (True, "replied")
