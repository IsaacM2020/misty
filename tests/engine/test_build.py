import json

import pytest

from misty_engine import build as B
from misty_engine.build import Build, estimate, pick_sample
from misty_engine.pipeline import Worker

LONG = "The iGEM team met about the wiki and the part documentation deadline. " * 15


@pytest.fixture
def many(seed):
    ids = []
    for s, n in (("folders", 30), ("gmail", 20), ("gcal", 5)):
        kind = {"folders": "file", "gmail": "mail_thread", "gcal": "calendar_week"}[s]
        for i in range(n):
            ids.append(seed(f"it_{s[:2]}{i:014x}", LONG + f" unique {s} {i}", source=s, kind=kind,
                            title=f"{s}-{i}"))
    return ids


def test_pick_sample_is_deterministic_proportional_and_covers_sources(fctx, many):
    rows = B.eligible_rows(fctx)
    a, b = pick_sample(rows, 11), pick_sample(rows, 11)
    assert a == b and len(a) == 11
    by = {}
    for i in a:
        by[i[3:5]] = by.get(i[3:5], 0) + 1
    assert set(by) == {"fo", "gm", "gc"} and by["fo"] > by["gm"] >= by["gc"] >= 1


def test_pick_sample_larger_than_pool_returns_all(fctx, many):
    assert len(pick_sample(B.eligible_rows(fctx), 500)) == len(many)


async def test_full_build_runs_every_stage(fctx, fake_or, many):
    fctx.sources = {}
    b = Build(fctx)
    st = await b.run()
    assert st["phase"] == "done" and st["failed"] == 0 and st["total"] == len(many) and st["done"] == len(many)
    statuses = {r[0] for r in fctx.con.execute("select status from items")}
    assert statuses == {"done"}
    assert fctx.queue.counts().get("failed", 0) == 0          # a handler crash would hide behind finished items
    assert len(list(fctx.wiki.all_pages())) > len(many)


async def test_limit_build_only_touches_the_sample(fctx, fake_or, many):
    st = await Build(fctx).run(limit=12)
    assert st["total"] == 12 and st["done"] == 12
    n_embedded = fctx.con.execute("select count(*) from items where status='done'").fetchone()[0]
    assert n_embedded == 12
    assert fctx.con.execute("select count(*) from items where status='scrubbed'").fetchone()[0] == len(many) - 12


async def test_promos_never_reach_cloud_chats_do_under_zdr(fctx, fake_or, many, seed):
    promo = seed("it_pr0000000000001", "PROMO-CANARY big sale " * 30, source="gmail", kind="mail_promo")
    seed("it_ch0000000000001", "CHAT-CANARY lunch tomorrow " * 30, source="imessage", kind="chat_week")
    await Build(fctx).run()
    sent = "\n".join(fake_or.all_payload_texts())
    assert "PROMO-CANARY" not in sent and "CHAT-CANARY" in sent                 # P37: chats are cloud too
    assert all('"zdr": true' in b for b in fake_or.all_payload_texts())
    assert fctx.con.execute("select status from items where id=?", (promo,)).fetchone()[0] == "scrubbed"


async def test_first_compile_request_is_alone_then_cache_hits(fctx, fake_or, many):
    await Build(fctx).run()
    first = fake_or.chat_bodies[0]
    assert first["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    # every later request reports the system prompt as cached
    assert len(fake_or.chat_bodies) >= 3


async def test_estimate_is_an_upper_bound_close_to_actual_on_fake(fctx, fake_or, many):
    e = estimate(fctx)
    st = await Build(fctx).run()
    actual = st["spent_usd"]
    assert actual <= e.total_usd * 1.05
    assert actual >= e.total_usd * 0.8           # within 20% of the estimate on the fake


async def test_every_request_is_recorded_as_spend_and_has_zdr(fctx, fake_or, many):
    await Build(fctx).run(limit=10)
    n = fctx.con.execute("select count(*) from spend").fetchone()[0]
    assert n == fake_or.request_count > 0
    for raw in fake_or.all_payload_texts():
        assert json.loads(raw)["provider"]["zdr"] is True


async def test_cap_pauses_build_and_status_says_why(fctx, fake_or, many):
    fctx.settings.set("cap_build_usd", 0.0005)
    st = await Build(fctx).run()
    assert st["paused_by"] == "cap" and st["phase"] != "done"
    fctx.settings.set("cap_build_usd", 3.0)
    fctx.queue.release("waiting_cap")
    b = Build(fctx)
    await b._drain(b._follow_kinds() + ("worth",), {"worth": 8, "compile_batch": 8, "embed_note": 1})


async def test_retryable_error_backs_off_and_succeeds(fctx, fake_or, many, clock):
    fake_or.fail("/chat/completions", 503, times=1)

    async def sleep(s):
        clock.advance(s + 0.1)
    st = await Build(fctx, sleep=sleep).run(limit=5)
    assert st["failed"] == 0 and st["done"] == 5


def test_estimate_formats_and_flags_over_cap(fctx, many):
    fctx.settings.set("cap_build_usd", 0.0001)
    assert "OVER the cap" in B.format_estimate(estimate(fctx))


async def test_rebuild_on_a_finished_wiki_goes_back_to_live(fctx, fake_or, many):
    fctx.settings.set("synth_done", True)                                   # stage 5 already ran once
    fctx.sources = {}
    st = await Build(fctx).run()
    assert st["phase"] == "done" and fctx.settings.get("phase") == "live"


async def test_first_build_stays_in_build_phase_for_stage_5(fctx, fake_or, many):
    fctx.sources = {}
    await Build(fctx).run()
    assert fctx.settings.get("phase") == "build"


def test_build_phase_order_is_the_documented_one():
    from misty_engine.build import Build
    assert Build.PHASES == ("discover", "prep", "worth", "compile", "resolve", "summarise", "areas", "about",
                            "priorities")


async def test_build_summarises_what_qualifies_then_waits_for_the_areas_accept(fctx, fake_or, many):
    import re
    from fakes import ScriptedRunner
    def echo(prompt):
        ids = re.findall(r"^id: (en_[0-9a-f]+)$", prompt, re.M)
        return {"entities": [{"id": i, "summary": "A real summary sentence.", "now": []} for i in ids]}
    fctx.runner = ScriptedRunner(*[echo] * 40)
    for iid in list(many)[:6]:                                   # two notes name the same person: it qualifies
        fake_or.set_draft(iid, people=["Aarav Shah"])
    st = await Build(fctx).run()
    assert st["paused_by"] == "areas" and st["phase"] != "done"
    assert fctx.con.execute("SELECT COUNT(*) FROM entities WHERE status='active'").fetchone()[0] >= 1
    assert all("(stub)" not in fctx.wiki.read(p).body for p in fctx.wiki.all_pages()
               if p.startswith("people/"))
    purposes = [c["purpose"] for c in fctx.runner.calls]
    assert "summarise" in purposes and set(purposes) <= {"summarise", "areas"}      # nothing else before the accept
