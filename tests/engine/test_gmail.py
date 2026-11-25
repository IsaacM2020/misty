import pytest

from fakes_google import FakeGmail, make_thread
from misty_engine.sources.gmail import GmailSource, measure


def test_gmail_first_sync_saves_history_id_then_incremental(clock):
    g = FakeGmail()
    src = GmailSource(g.factory, clock=clock)
    items = list(src.discover(None))
    cur = src.next_cursor()
    assert len(items) == 3 and cur == g.history_id
    assert items[0].source == "gmail" and items[0].locator.startswith("gmail:thread:")
    g.add_thread("t4")
    src2 = GmailSource(g.factory, clock=clock)
    assert [i.locator for i in src2.discover(cur)] == ["gmail:thread:t4"]
    assert src2.next_cursor() == g.history_id


def test_gmail_history_404_triggers_resync(clock):
    g = FakeGmail()
    g.expire_history = True
    src = GmailSource(g.factory, clock=clock)
    items = list(src.discover("1"))
    assert len(items) == 3 and src.next_cursor() == g.history_id
    assert any("after:" in q for q in g.list_queries)


def test_gmail_promo_is_skipped_before_it_is_stored(clock):
    g = FakeGmail([make_thread("p1", labels=("INBOX", "CATEGORY_PROMOTIONS")), make_thread("n1")])
    src = GmailSource(g.factory, clock=clock)
    kinds = {i.locator: i.kind for i in src.discover(None)}
    assert kinds == {"gmail:thread:n1": "mail_thread"}
    assert src.stats["skip_promotions"] == 1 and src.stats["keep_human"] == 1


def test_gmail_limit_counts_survivors_and_drops_the_cursor(clock):
    g = FakeGmail([make_thread("p1", labels=("CATEGORY_PROMOTIONS",)), make_thread("a"), make_thread("b"),
                   make_thread("c")])
    src = GmailSource(g.factory, clock=clock)
    assert [i.locator for i in src.discover(None, limit=2)] == ["gmail:thread:a", "gmail:thread:b"]
    assert src.next_cursor() is None            # truncated: the next full discover must find the rest


def test_measure_reports_survivors_and_skip_counts(clock):
    g = FakeGmail([make_thread("p", labels=("CATEGORY_PROMOTIONS",), body="sale " * 50),
                   make_thread("n", sender="GitHub <notifications@github.com>"),
                   make_thread("h1", body="word " * 100), make_thread("h2", body="word " * 100)])
    m = measure(g.factory, sample=5, clock=clock)
    assert m["listed"] == 4 and m["threads"] == 2
    assert m["skipped"] == {"promotions": 1, "automated_sender": 1} and m["kept"] == {"human": 2}


def test_gmail_item_meta_has_sender_and_rev(clock):
    g = FakeGmail([make_thread("a", subject="Plan", sender="Aarav Shah <aarav@example.com>")])
    it = next(GmailSource(g.factory, clock=clock).discover(None))
    assert it.title == "Plan" and it.meta["sender"] == "aarav@example.com" and it.meta["rev"] == "100"


def test_gmail_load_text_cleans_bodies(clock):
    g = FakeGmail([make_thread("a", subject="Plan", body="See you.\n\nOn Mon, X wrote:\n> old")])
    src = GmailSource(g.factory, clock=clock)
    text = src.load_text(next(src.discover(None)))
    assert text.startswith("Subject: Plan") and "See you." in text and "old" not in text


def test_gmail_html_only_message(clock):
    g = FakeGmail([make_thread("h", body="<p>Hello</p><p>World</p>", html=True)])
    src = GmailSource(g.factory, clock=clock)
    assert "Hello\nWorld" in src.load_text(next(src.discover(None)))


def test_gmail_is_read_only(clock):
    g = FakeGmail()
    src = GmailSource(g.factory, clock=clock)
    for it in src.discover(None):
        src.load_text(it)
    assert g.calls_used <= {"getProfile", "threads.list", "threads.get", "history.list"}


def test_measure_counts_and_averages(clock):
    g = FakeGmail([make_thread(f"t{i}", body="word " * 100) for i in range(10)])
    m = measure(g.factory, sample=5, clock=clock)
    assert m["threads"] == 10 and m["avg_tokens"] > 50 and m["est_total_tokens"] == round(10 * m["avg_tokens"])


def test_rate_limit_403_is_retried_not_needs_auth(clock):
    g = FakeGmail([make_thread(f"t{i}") for i in range(30)])
    g.rate_limits = 7
    sleeps = []
    src = GmailSource(g.factory, clock=clock, sleep=sleeps.append, pace=0)
    assert len(list(src.discover(None))) == 30
    assert sleeps and sleeps[0] == 65.0          # a limit window is a minute: wait it out
    assert src.pace > 0                           # and go slower afterwards


def test_rate_limit_that_never_clears_is_retryable(clock):
    from misty_engine.errors import Retryable
    g = FakeGmail()
    g.rate_limits = 10 ** 6
    src = GmailSource(g.factory, clock=clock, sleep=lambda s: None, pace=0)
    with pytest.raises(Retryable):
        list(src.discover(None))


def test_real_permission_error_is_still_needs_auth(clock):
    from fakes_google import http_error
    from misty_engine.errors import Fatal
    from misty_engine.sources.gmail import _wrap
    with pytest.raises(Fatal) as ei:
        _wrap(lambda: (_ for _ in ()).throw(http_error(403, "insufficientPermissions")))
    assert ei.value.code == "NEEDS_AUTH"


def test_cache_resumes_after_a_failure_and_skips_unchanged_threads(clock, tmp_path):
    from misty_engine.errors import Retryable
    path = tmp_path / "v.json"
    g = FakeGmail([make_thread(f"t{i}") for i in range(60)])
    g.rate_limits = 10 ** 6
    with pytest.raises(Retryable):
        measure(g.factory, sample=2, clock=clock, cache_path=path, sleep=lambda s: None, pace=0)
    g.rate_limits = 0
    m = measure(g.factory, sample=2, clock=clock, cache_path=path, sleep=lambda s: None, pace=0)
    assert m["listed"] == 60 and m["threads"] == 60
    # a third run needs no metadata fetches at all
    g.rate_limits = 10 ** 6
    m2 = measure(g.factory, sample=0, clock=clock, cache_path=path, sleep=lambda s: None, pace=0)
    assert m2["threads"] == 60 and m2["kept"] == {"human": 60}


def test_cache_rereads_a_thread_whose_history_id_changed(clock, tmp_path):
    path = tmp_path / "v.json"
    g = FakeGmail([make_thread("a", labels=("INBOX", "CATEGORY_PROMOTIONS"), history_id="1")])
    kw = dict(clock=clock, cache_path=path, sleep=lambda s: None, pace=0)
    assert measure(g.factory, sample=1, **kw)["threads"] == 0
    g.data["a"] = make_thread("a", labels=("INBOX", "CATEGORY_PROMOTIONS", "STARRED"), history_id="2")
    assert measure(g.factory, sample=1, **kw)["kept"] == {"starred": 1}


def test_cached_kept_threads_still_become_items(clock, tmp_path):
    path = tmp_path / "v.json"
    g = FakeGmail([make_thread("a", subject="Plan", sender="Aarav <aarav@example.com>")])
    list(GmailSource(g.factory, clock=clock, cache_path=path, pace=0).discover(None))
    g.rate_limits = 10 ** 6                      # a second run must not need the API for metadata
    it = next(GmailSource(g.factory, clock=clock, cache_path=path, pace=0).discover(None))
    assert it.title == "Plan" and it.meta["sender"] == "aarav@example.com"


def test_old_cached_replied_verdicts_are_looked_at_again(clock, tmp_path):
    import json as _json
    path = tmp_path / "v.json"
    g = FakeGmail([make_thread("a", sender="Immich <you@example.com>", labels=("SENT",),
                               headers={"To": "you@example.com"}, history_id="7")])
    path.write_text(_json.dumps({"a": {"h": "7", "r": "replied", "k": True, "i": None}}))       # version 1 entry
    m = measure(g.factory, sample=0, clock=clock, cache_path=path, sleep=lambda s: None, pace=0)
    assert m["kept"] == {"human": 1}


def test_full_thread_fetch_waits_out_a_rate_limit_instead_of_failing(clock):
    g = FakeGmail([make_thread("a", body="Hello there.")])
    sleeps = []
    src = GmailSource(g.factory, clock=clock, sleep=sleeps.append, pace=0)
    item = next(src.discover(None))
    orig = g.threads().get
    state = {"n": 2}

    class T:
        def get(self_inner, userId, id, format="full", metadataHeaders=None):
            if state["n"] > 0:
                state["n"] -= 1
                from fakes_google import _Req, http_error
                return _Req(lambda: (_ for _ in ()).throw(http_error(403, "rateLimitExceeded")))
            return orig(userId=userId, id=id, format=format, metadataHeaders=metadataHeaders)
    g.threads = lambda: T()
    assert "Hello there." in src.load_text(item)
    assert sleeps.count(65.0) == 2 and src.load_gap > 0.25


def test_gmail_item_meta_has_sender_handle(clock):
    g = FakeGmail([make_thread("a", subject="Plan", sender="Aarav Shah <Aarav@School.edu>")])
    it = next(GmailSource(g.factory, clock=clock).discover(None))
    assert it.meta["sender_handle"] == ["email", "aarav@school.edu"]
