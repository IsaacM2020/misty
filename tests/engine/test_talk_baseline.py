"""F0: scripts/checks/talk_baseline.py on a made-up log (no real turns, no model), and the new prep marks in chat.send."""
import asyncio
import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "checks"))
spec = importlib.util.spec_from_file_location("talk_baseline", ROOT / "scripts" / "checks" / "talk_baseline.py")
tb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tb)

T0 = 1794000000.0


def stamp(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def turn_lines(i, *, launch=None, wake=None, prep=400, connect=1200, model=150, first_chunk=2400, tool=None, t_tap=None):
    """One app line and one engine line. Total = first_chunk + out_latency(=1)."""
    t_tap = t_tap or T0 + i * 600
    tid = f"v{i:08x}"
    app = (f"{stamp(t_tap + 3)} TURN_TIMING turn={tid} t_tap={t_tap:.3f} since_wake_s={wake if wake is not None else 'none'} "
           f"since_launch_s={launch if launch is not None else 9999} speech_s=1.0 first_clause_chars=50 out_latency=1 tap_end=0 "
           f"mic_stopped=8 transcript=160 send=170 first_delta={first_chunk - 300} first_clause={first_chunk - 250} "
           f"speak_called={first_chunk - 240} drain_start={first_chunk - 230} engine_ready={first_chunk - 100} first_chunk={first_chunk}")
    t0 = t_tap + 0.172
    http0 = prep
    hdr = http0 + connect
    tok = hdr + model
    body = f"r0_http_start={http0} r0_headers={hdr} r0_first_token={tok} first_delta_out={tok}"
    if tool:
        body += f" r0_tool_{tool}_start={tok + 10} r0_tool_{tool}_end={tok + 3010}"
    eng = f"{stamp(t_tap + 3)} ENGINE_TIMING turn={tid} t0={t0:.3f} since_start_s=100 lock_got=0 recv=0 prompt_ready={prep - 10} {body} end={tok + 50}"
    return app, eng, tid, t0, http0, hdr


def or_line(epoch, tokens_in, cached, job="None", model="anthropic/claude-haiku-5.5"):
    return f"{stamp(epoch)} INFO OR_CALL job_id={job} model={model} ms=1000 usd=0.0004 http=200 tokens_in={tokens_in} cached={cached}"


def build(tmp_path, specs, join=True):
    v, e = [], []
    for i, kw in enumerate(specs):
        cached = kw.pop("cached", 0)
        app, eng, tid, t0, http0, hdr = turn_lines(i, **kw)
        v += [app, eng]
        if join and cached is not None:
            e.append(or_line(t0 + hdr / 1000 + 0.3, 5000, cached))
            e.append(or_line(t0 + hdr / 1000 + 0.4, 800, 0, job="jb_other"))                # a background job: must not be joined
    (tmp_path / "voice.log").write_text("\n".join(v) + "\n")
    (tmp_path / "engine.log").write_text("\n".join(e) + "\n")
    return tmp_path / "voice.log", tmp_path / "engine.log"


def test_groups_and_medians(tmp_path):
    specs = [dict(launch=30, first_chunk=3400, cached=0), dict(launch=60, first_chunk=3600, cached=0)]
    specs += [dict(launch=None, first_chunk=2000 + 100 * k, cached=4000) for k in range(5)]
    specs += [dict(launch=None, first_chunk=6000, tool="calendar", cached=0)]
    specs += [dict(launch=None, wake=20, first_chunk=3000, cached=0)]
    v, e = build(tmp_path, specs)
    turns = tb.load(v, e)
    assert len(turns) == 9
    g = tb.group(turns)
    assert len(g[("cold", "plain")]) == 2 and len(g[("warm", "plain")]) == 5 and len(g[("warm", "tool")]) == 1 and len(g[("wake", "plain")]) == 1
    st = tb.stats([t["total"] for t in g[("warm", "plain")]])
    assert st["n"] == 5 and st["median"] == 2201 and st["p90"] >= st["median"]
    assert tb.stats([])["median"] is None


def test_every_slice_is_in_the_table_for_plain_and_tool(tmp_path):
    specs = [dict(first_chunk=2000, cached=0) for _ in range(4)] + [dict(first_chunk=6000, tool="mail", cached=0) for _ in range(3)]
    v, e = build(tmp_path, specs)
    text = tb.report(tb.load(v, e), limit=2506)
    for name in ("hear", "to_engine", "prep", "connect", "model", "tools", "event", "clause", "voice_wait", "audio_start", "synth", "out", "TOTAL"):
        assert name in text
    assert "plain" in text and "tool" in text and "cached_pct" in text
    assert "endpoint" in text.lower() and "650" in text                  # the quiet that ends his speech is not in TOTAL, said once


def test_cached_pct_is_joined_by_time_and_never_guessed(tmp_path):
    v, e = build(tmp_path, [dict(cached=4000), dict(cached=0)])
    a, b = tb.load(v, e)
    assert a["cached_pct"] == 80.0 and b["cached_pct"] == 0.0           # the background job call is not counted
    v, e = build(tmp_path, [dict(cached=4000)], join=False)
    (t,) = tb.load(v, e)
    assert t["cached_pct"] is None and "unknown" in tb.report([t], limit=2506)
    (t,) = tb.load(v, tmp_path / "missing.log")
    assert t["cached_pct"] is None


def test_verdict_cold_turns_when_warm_is_under_the_limit(tmp_path):
    specs = [dict(launch=20 + k, first_chunk=3800) for k in range(16)] + [dict(first_chunk=2100) for _ in range(14)]
    v, e = build(tmp_path, specs)
    out = tb.verdict(tb.load(v, e), limit=2506)
    assert out["cause"] == "cold" and out["n"] == 30 and out["warm_median"] == 2101 and out["all_median"] > 2506
    assert "F1" in out["text"] and "part 1b" not in out["text"]


def test_verdict_prep_when_warm_alone_is_over(tmp_path):
    specs = [dict(first_chunk=2700) for _ in range(25)] + [dict(launch=10, first_chunk=3500) for _ in range(5)]
    v, e = build(tmp_path, specs)
    out = tb.verdict(tb.load(v, e), limit=2506)
    assert out["cause"] == "prep" and "1b" in out["text"]


def test_verdict_is_within_the_limit_or_unknown(tmp_path):
    v, e = build(tmp_path, [dict(first_chunk=2000) for _ in range(12)])
    assert tb.verdict(tb.load(v, e), limit=2506)["cause"] == "none"
    v, e = build(tmp_path, [dict(first_chunk=2000) for _ in range(3)])
    assert tb.verdict(tb.load(v, e), limit=2506)["cause"] == "unknown"           # under 10 plain turns: no verdict
    v, e = build(tmp_path, [dict(launch=10, first_chunk=3900) for _ in range(12)])
    assert tb.verdict(tb.load(v, e), limit=2506)["cause"] == "unknown"           # no warm turn to compare


def test_last_30_plain_turns_only_and_tool_turns_are_left_out(tmp_path):
    specs = [dict(first_chunk=9000) for _ in range(10)] + [dict(first_chunk=2000) for _ in range(30)] + [dict(first_chunk=9000, tool="mail") for _ in range(3)]
    v, e = build(tmp_path, specs)
    out = tb.verdict(tb.load(v, e), limit=2506)
    assert out["n"] == 30 and out["all_median"] == 2001


def test_write_appends_a_dated_table_and_only_rebaselines_when_asked(tmp_path):
    base = tmp_path / "baselines.md"
    base.write_text("# base\nfirst_word_median_ms: 2278\n")
    v, e = build(tmp_path, [dict(first_chunk=2100) for _ in range(12)])
    turns = tb.load(v, e)
    tb.write(base, turns, limit=2506, rebaseline=False, today="2026-11-17")
    s = base.read_text()
    assert "## Re-measure (2026-11-17)" in s and "first_word_median_ms: 2278" in s
    tb.write(base, turns, limit=2506, rebaseline=False, today="2026-11-18")
    assert base.read_text().count("## Re-measure") == 2 and "first_word_median_ms: 2278" in base.read_text()
    tb.write(base, turns, limit=2506, rebaseline=True, today="2026-11-19")
    assert "first_word_median_ms: 2101" in base.read_text() and "first_word_median_ms: 2278" not in base.read_text()


def test_runs_on_the_command_line_with_a_made_up_log(tmp_path, capsys):
    v, e = build(tmp_path, [dict(first_chunk=2100) for _ in range(12)])
    base = tmp_path / "baselines.md"
    base.write_text("first_word_median_ms: 2278\n")
    assert tb.main(["--log", str(v), "--engine-log", str(e), "--baseline", str(base)]) == 0
    assert "TOTAL" in capsys.readouterr().out and base.read_text() == "first_word_median_ms: 2278\n"       # no --write: nothing changes


def test_a_missing_log_says_so(tmp_path, capsys):
    assert tb.main(["--log", str(tmp_path / "nope.log"), "--engine-log", str(tmp_path / "x"), "--baseline", str(tmp_path / "b.md")]) == 1
    assert "no TURN_TIMING" in capsys.readouterr().out


# ---- the marks inside chat.send --------------------------------------------------------------------------------------------
def test_chat_send_marks_the_prep_steps_in_order(ctx):
    from misty_engine import chat
    from misty_engine.turn_timing import Marks
    from test_chat import ScriptBrain, nothing, no
    ctx.openrouter = ScriptBrain({"content": "Fine thanks."})
    marks = Marks()
    asyncio.run(chat.send(ctx, "how are you", on_delta=nothing, on_status=nothing, confirm=no, voice=True, marks=marks))
    names = [n for n, _ in marks.items]
    want = ["recv", "frame_ready", "prompt_built", "prompt_ready", "tools_built", "user_saved", "preflight_done", "r0_post"]
    pos = [names.index(w) for w in want]
    assert pos == sorted(pos)
    line = marks.line("v1")
    assert all(f" {w}=" in line for w in want)


def test_no_marks_no_change(ctx):
    from misty_engine import chat
    from test_chat import ScriptBrain, nothing, no
    ctx.openrouter = ScriptBrain({"content": "Fine thanks."})
    out = asyncio.run(chat.send(ctx, "how are you", on_delta=nothing, on_status=nothing, confirm=no, voice=True))
    assert "Fine" in out["reply"]


def test_typed_turns_report_engine_slices_only(ctx):
    from test_chat import ScriptBrain
    ctx.openrouter = ScriptBrain(*[{"content": "ok"}] * 3)
    rows = asyncio.run(tb.typed_run(ctx, 3, ["hello there", "what is a tide", "tell me a joke"]))
    assert len(rows) == 3 and all("prompt_built" in r and "r0_post" in r for r in rows)


# ---- F1: the warm lines in engine.log ---------------------------------------------------------------------------------
def test_warm_summary_counts_sent_and_skipped_and_cost(tmp_path):
    log = tmp_path / "engine.log"
    log.write_text("\n".join([
        "2026-11-18T01:00:00Z INFO WARM kind=sent cached=0 ms=900 usd=0.0021",
        "2026-11-18T01:30:00Z INFO WARM kind=sent cached=9000 ms=700 usd=0.0004",
        "2026-11-18T01:31:00Z INFO WARM kind=skipped why=recent cached=0 ms=0 usd=0.0",
        "2026-11-18T01:32:00Z INFO WARM kind=skipped why=class cached=0 ms=0 usd=0.0",
        "2026-11-18T01:33:00Z INFO WARM kind=skipped why=recent cached=0 ms=0 usd=0.0",
        "2026-11-19T09:00:00Z INFO WARM kind=sent cached=0 ms=800 usd=0.0020",
        "2026-11-19T09:00:05Z INFO OR_CALL model=m ms=1 usd=0.1 http=200 tokens_in=1 cached=0"]) + "\n")
    text = tb.warm_summary(log)
    assert "2026-11-18: sent 2, skipped class 1, recent 2, cost $0.0025" in text
    assert "2026-11-19: sent 1, skipped none, cost $0.0020" in text
    assert tb.warm_summary(tmp_path / "missing.log") == "" and tb.warm_summary(tmp_path / "x") == ""


def test_the_report_carries_the_warm_text_after_the_verdict():

    assert tb.report([], 2506.0, "Warms (F1): none").rstrip().endswith("Warms (F1): none")


def test_the_warm_log_line_is_allowed_and_has_the_card_fields(ctx, misty_home):
    import logging
    from misty_engine import chat
    from misty_engine.logs import setup_logging
    from test_chat_warm import WarmBrain
    setup_logging(misty_home.logs)
    ctx.openrouter = WarmBrain(cached=5)
    asyncio.run(chat.warm(ctx))
    for h in logging.getLogger("misty").handlers:
        h.flush()
    text = "".join(p.read_text() for p in misty_home.logs.glob("engine.log*"))
    line = next(ln for ln in text.splitlines() if " WARM " in ln)
    assert "kind=sent" in line and "cached=5" in line and "ms=" in line and "usd=0.0009" in line


def test_early_summary_counts_early_ends_and_merges(tmp_path):
    log = tmp_path / "voice.log"
    log.write_text("2026-11-17T01:00:00Z EARLY_END quiet_ms=350 read_ms=140\n"
                   "2026-11-17T01:00:05Z EARLY_END quiet_ms=350 read_ms=120\n"
                   "2026-11-17T01:00:06Z merged into the last message\n"
                   "2026-11-17T01:00:06Z EARLY_END merged=true\n"
                   "2026-11-17T01:01:00Z EARLY_END quiet_ms=350 read_ms=150\n"
                   "2026-11-17T01:01:00Z EARLY_END quiet_ms=350 read_ms=160\n")
    text = tb.early_summary(log)
    assert "4 phrases ended at 350 ms" in text and "1 of them merged" in text and "25 percent" in text
    assert tb.early_summary(tmp_path / "missing.log") == ""
    empty = tmp_path / "e.log"
    empty.write_text("2026-11-17T01:00:00Z phrase 2.0s\n")
    assert tb.early_summary(empty) == ""
