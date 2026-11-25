import pytest

from misty_engine.errors import CapHit, Fatal, NetworkDown, Retryable
from misty_engine.jobs import JobQueue

IT = "it_0000000000000001"


@pytest.fixture
def q(con, clock, settings):
    return JobQueue(con, clock, settings)


def test_enqueue_idempotent_while_active(q):
    assert q.enqueue("prep", IT) == q.enqueue("prep", IT)


def test_enqueue_again_after_done(q):
    a = q.enqueue("prep", IT)
    [j] = q.claim(["prep"], 5)
    q.complete(j.id)
    assert q.enqueue("prep", IT) != a


def test_retryable_backoff_then_failed(q, clock):
    jid = q.enqueue("compile", IT, cloud=True)
    for delay in (5, 30, 120):
        [j] = q.claim(["compile"], 1)
        assert q.fail(j.id, Retryable("HTTP_503")) == "queued"
        assert q.claim(["compile"], 1) == []
        clock.advance(delay)
    [j] = q.claim(["compile"], 1)
    assert q.fail(j.id, Retryable("HTTP_503")) == "failed"
    assert q.get(jid).error_code == "HTTP_503"


def test_network_down_waits_without_spending_attempt(q):
    jid = q.enqueue("compile", IT, cloud=True)
    [j] = q.claim(["compile"], 1)
    assert q.fail(j.id, NetworkDown()) == "waiting_network"
    assert q.release("waiting_network") == 1
    assert q.get(jid).status == "queued" and q.get(jid).attempts == 0


def test_cap_hit_waits(q):
    q.enqueue("compile", IT)
    [j] = q.claim(["compile"], 1)
    assert q.fail(j.id, CapHit("CAP_DAILY")) == "waiting_cap"


def test_sub_cap_hit_waits_and_is_released_by_the_budget_window(q):
    jid = q.enqueue("synth", None)
    [j] = q.claim(["synth"], 1)
    assert q.fail(j.id, CapHit("CAP_PURPOSE:judge")) == "waiting_cap"
    assert q.get(jid).error_code == "CAP_PURPOSE:judge"
    assert q.release("waiting_cap") == 1
    assert q.get(jid).status == "queued"


def test_leftover_waiting_quota_row_is_just_queued_work_after_restart(q, con):
    """The old subscription runner could park a job in waiting_quota. Nothing produces it now; start-up releases it."""
    jid = q.enqueue("synth", None)
    con.execute("UPDATE jobs SET status='waiting_quota' WHERE id=?", (jid,))
    assert q.recover_running() == 1
    assert q.get(jid).status == "queued"


def test_fatal_fails_and_manual_retry_resets(q):
    jid = q.enqueue("compile", IT)
    [j] = q.claim(["compile"], 1)
    assert q.fail(j.id, Fatal("KEY_INVALID")) == "failed"
    q.retry(jid)
    assert q.get(jid).status == "queued" and q.get(jid).attempts == 0


def test_recover_running_requeues(q):  # Review Focus 1: engine killed mid-job
    jid = q.enqueue("embed_note", IT)
    q.claim(["embed_note"], 1)
    assert q.recover_running() == 1
    j = q.get(jid)
    assert j.status == "queued" and j.attempts == 0


def test_paused_claims_nothing(q, settings):
    q.enqueue("prep", IT)
    settings.set("paused", True)
    assert q.claim(["prep"], 5) == []


def test_claim_respects_kinds_and_limit(q):
    for i in range(5):
        q.enqueue("prep", f"it_{i:016x}")
    q.enqueue("compile", "it_00000000000000ff")
    assert len(q.claim(["prep"], 3)) == 3
    assert [j.kind for j in q.claim(["compile"], 5)] == ["compile"]


def test_counts_and_pending(q):
    q.enqueue("prep", IT)
    assert q.counts() == {"queued": 1} and q.pending(["prep"]) == 1 and q.pending(["compile"]) == 0


def test_the_same_rescan_waiting_is_not_added_twice(q):
    assert q.enqueue("discover", payload={"source": "mail"}) == q.enqueue("discover", payload={"source": "mail"})
    assert q.enqueue("discover", payload={"source": "notes"}) != q.enqueue("discover", payload={"source": "mail"})


def test_changed_files_join_the_waiting_batch(q):
    a = q.enqueue_paths("discover_paths", ["/a", "/b"])
    assert q.enqueue_paths("discover_paths", ["/b", "/c"]) == a
    assert q.get(a).payload["paths"] == ["/a", "/b", "/c"]
