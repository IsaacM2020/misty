from misty_engine.api import build_methods, status_snapshot
from misty_engine.ctx import Ctx
from misty_engine.jobs import JobQueue
from misty_engine.net import NetState
from misty_engine.openrouter import OpenRouterClient
from misty_engine.spend import SpendMeter


def _ctx(con, settings, clock, pinned, misty_home):
    net = NetState()
    spend = SpendMeter(con, clock, settings)
    return Ctx(paths=misty_home, pinned=pinned, con=con, settings=settings, queue=JobQueue(con, clock, settings),
               spend=spend, net=net, clock=clock,
               openrouter=OpenRouterClient(pinned=pinned, settings=settings, spend=spend, net=net,
                                           key_provider=lambda: None, clock=clock))


async def test_status_shape_and_controls(con, settings, clock, pinned, misty_home):
    ctx = _ctx(con, settings, clock, pinned, misty_home)
    events = []

    async def notify(m, p):
        events.append(m)
    ctx.notify = notify
    m = build_methods(ctx)
    st = await m["status.get"]({})
    assert set(st) == {"online", "paused", "queue", "capped", "spend", "build", "sources", "badge", "health"}
    assert st["spend"]["cap_daily"] == 0.20 and st["online"] is True
    await m["control.pause"]({})
    assert status_snapshot(ctx)["paused"] is True
    await m["net.set"]({"online": False})
    assert status_snapshot(ctx)["online"] is False
    assert events.count("status.changed") == 2


async def test_jobs_skipped_while_paid_calls_were_off_are_not_failures(con, settings, clock, pinned, misty_home):
    ctx = _ctx(con, settings, clock, pinned, misty_home)
    for i, code in enumerate(["PAID_LOCKED", "PAID_LOCKED", "UNEXPECTED"]):
        jid = ctx.queue.enqueue("summarise", f"it_{i}")
        con.execute("UPDATE jobs SET status='failed', error_code=? WHERE id=?", (code, jid))
    q = status_snapshot(ctx)["queue"]
    assert q["failed"] == 1 and q["skipped"] == 2
