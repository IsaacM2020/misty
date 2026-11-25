"""F6: the opt-in collector of heard phrases, its pruning, and the labelling loop. Free: no mic, no model."""
import asyncio
import sys
from pathlib import Path

import pytest
import yaml

from misty_engine import phrases, setting

DAY = 86400


def run(c):
    return asyncio.run(c)


def on(ctx):
    phrases.set_collecting(ctx, True)


def add(ctx, text="open my calendar please", **kw):
    kw.setdefault("outcome", "delivered")
    return phrases.add(ctx, text, **kw)


def rows(ctx):
    return [dict(r) for r in ctx.con.execute("SELECT * FROM heard_phrases ORDER BY id")]


# ---- the collector ---------------------------------------------------------------------------------------------------
def test_off_by_default_writes_nothing(ctx):
    assert phrases.collecting(ctx) is False
    assert add(ctx) == {"kept": False, "why": "off"}
    assert rows(ctx) == []


def test_on_keeps_scrubbed_text_with_the_route_facts(ctx):
    on(ctx)
    r = add(ctx, "my password: hunter22 for the plan", state="speaking", named=True, followup=True, since_reply=4.26, outcome="held")
    assert r == {"kept": True}
    (row,) = rows(ctx)
    assert "hunter22" not in row["text"] and "REDACTED" in row["text"]
    assert (row["state"], row["named"], row["followup"], row["since_reply"], row["outcome"]) == ("speaking", 1, 1, 4.3, "held")
    assert row["label"] is None and row["labelled_at"] is None


def test_a_card_number_is_scrubbed(ctx):
    on(ctx)
    add(ctx, "my card is 4111 1111 1111 1111 thanks")
    assert "4111" not in rows(ctx)[0]["text"]


def test_class_mode_and_private_app_write_nothing(ctx):
    on(ctx)
    assert add(ctx, class_mode=True) == {"kept": False, "why": "class"}
    assert add(ctx, private=True) == {"kept": False, "why": "private"}
    assert rows(ctx) == []


def test_bad_values_and_noise_are_not_kept(ctx):
    on(ctx)
    assert add(ctx, state="thinking")["why"] == "bad"
    assert add(ctx, outcome="sent")["why"] == "bad"
    assert add(ctx, "...  ?!")["why"] == "empty"
    assert add(ctx, "")["why"] == "empty"
    assert rows(ctx) == []


def test_long_text_is_cut(ctx):
    on(ctx)
    add(ctx, "word " * 500)
    assert len(rows(ctx)[0]["text"]) <= phrases.CAP_CHARS


def test_since_reply_may_be_missing_or_junk(ctx):
    on(ctx)
    add(ctx, "first one here", since_reply=None)
    add(ctx, "second one here", since_reply="soon")
    assert [r["since_reply"] for r in rows(ctx)] == [None, None]


# ---- 14 days, labels, clear ------------------------------------------------------------------------------------------
def test_prune_keeps_labelled_rows_and_recent_ones(ctx):
    on(ctx)
    for t in ("old unlabelled one", "old labelled one", "recent one"):
        add(ctx, t)
    now = ctx.clock.now()
    ctx.con.execute("UPDATE heard_phrases SET at=? WHERE text IN ('old unlabelled one','old labelled one')", (now - 15 * DAY,))
    assert phrases.set_label(ctx.con, rows(ctx)[1]["id"], "for_her", now)
    assert phrases.prune(ctx) == 1
    assert [r["text"] for r in rows(ctx)] == ["old labelled one", "recent one"]


def test_add_prunes_by_itself(ctx):
    on(ctx)
    add(ctx, "ancient phrase here")
    ctx.con.execute("UPDATE heard_phrases SET at=?", (ctx.clock.now() - 20 * DAY,))
    add(ctx, "fresh phrase here")
    assert [r["text"] for r in rows(ctx)] == ["fresh phrase here"]


def test_clear_empties_everything_labelled_too(ctx):
    on(ctx)
    add(ctx, "one phrase here")
    phrases.set_label(ctx.con, rows(ctx)[0]["id"], "not_for_her", 1.0)
    assert phrases.clear(ctx) == 1 and rows(ctx) == []


def test_turning_it_off_stops_writing_but_keeps_what_is_there(ctx):
    on(ctx)
    add(ctx, "kept one here")
    phrases.set_collecting(ctx, False)
    assert add(ctx, "not kept here")["why"] == "off"
    assert len(rows(ctx)) == 1


def test_set_label_refuses_a_bad_label_or_id(ctx):
    on(ctx)
    add(ctx)
    i = rows(ctx)[0]["id"]
    assert not phrases.set_label(ctx.con, i, "maybe", 1.0)
    assert not phrases.set_label(ctx.con, 9999, "for_her", 1.0)
    assert rows(ctx)[0]["label"] is None


# ---- the setting tool ------------------------------------------------------------------------------------------------
async def _no(_q):
    return True


def test_setting_on_off_forget(ctx):
    r = run(setting.apply(ctx, "collect_phrases", "on", _no))
    assert r["ok"] and r["verified"] and r["value"] is True and "14 days" in r["note"]
    assert phrases.collecting(ctx)
    add(ctx)
    r = run(setting.apply(ctx, "collect_phrases", "forget", _no))
    assert r["ok"] and r["forgot"] == 1 and rows(ctx) == []
    r = run(setting.apply(ctx, "collect_phrases", "off", _no))
    assert r["ok"] and r["value"] is False and not phrases.collecting(ctx)
    assert not run(setting.apply(ctx, "collect_phrases", "banana", _no))["ok"]


def test_setting_is_in_the_tool_and_the_rule():
    from misty_engine import chat
    assert "collect_phrases" in setting.NAMES and "collect_phrases" in chat.SETTING_RULE and "14 days" in chat.SETTING_RULE
    tool = next(t for t in chat.TOOLS if t["function"]["name"] == "setting")
    assert "collect_phrases" in tool["function"]["parameters"]["properties"]["name"]["enum"]


def test_no_audio_and_no_model_in_the_module():
    src = (Path(phrases.__file__)).read_text()
    for bad in ("openrouter", "ctx.jev", "wav", ".m4a", "requests", "httpx"):
        assert bad not in src


# ---- the rpc ---------------------------------------------------------------------------------------------------------
def test_rpc_methods_exist_and_work(ctx):
    from misty_engine.api import build_methods
    m = build_methods(ctx)
    for k in ("phrases.add", "phrases.state", "phrases.clear"):
        assert k in m
    assert run(m["phrases.add"]({"text": "hello there friend", "state": "listening", "outcome": "dropped"}))["why"] == "off"
    assert run(m["phrases.state"]({}))["collecting"] is False
    on(ctx)
    assert run(m["phrases.add"]({"text": "hello there friend", "state": "listening", "outcome": "dropped"}))["kept"] is True
    assert run(m["phrases.add"]({"text": "secret class talk", "state": "listening", "outcome": "dropped", "class_mode": True}))["why"] == "class"
    assert run(m["phrases.state"]({}))["kept"] == 1
    assert run(m["phrases.clear"]({}))["cleared"] == 1


def test_scheduler_prunes_every_hour():
    src = (Path(phrases.__file__).parent / "scheduler.py").read_text()
    assert "phrases_prune" in src


# ---- the labelling loop ----------------------------------------------------------------------------------------------
def fill(ctx, n_her, n_no):
    on(ctx)
    for i in range(n_her):
        add(ctx, f"for her number {i} here", outcome="delivered" if i % 2 else "merged")
    for i in range(n_no):
        add(ctx, f"room talk number {i} here", outcome="dropped")
    ctx.con.execute("UPDATE heard_phrases SET at = at + id")                    # a known order: later rows are newer


def test_queue_is_balanced_and_never_returns_labelled_rows(ctx):
    fill(ctx, 6, 30)
    q = phrases.queue(ctx.con, 12)
    sides = ["her" if r["outcome"] != "dropped" else "no" for r in q]
    assert sides[:12] == ["her", "no"] * 6                                     # one of each, alternating
    phrases.set_label(ctx.con, q[0]["id"], "for_her", 1.0)
    assert q[0]["id"] not in [r["id"] for r in phrases.queue(ctx.con, 50)]


def test_queue_keeps_going_when_one_side_runs_out(ctx):
    fill(ctx, 2, 5)
    q = phrases.queue(ctx.con, 50)
    assert len(q) == 7 and [r["outcome"] == "dropped" for r in q][:4] == [False, True, False, True]


def test_queue_is_empty_with_nothing(ctx):
    assert phrases.queue(ctx.con) == []


def _script():
    sys.path.insert(0, str(Path(phrases.__file__).resolve().parents[3] / "scripts"))
    import label_phrases
    return label_phrases


def keys(*ks):
    it = iter(ks)
    return lambda _prompt="": next(it)


def test_labeller_labels_skips_quits_and_resumes(ctx, tmp_path):
    fill(ctx, 3, 3)
    ls = _script()
    out = []
    n = ls.run(ctx.con, keys("f", "n", "s", "q"), out.append, tmp_path / "a.yaml", now=5.0)
    assert n == {"labelled": 2, "skipped": 1}
    assert phrases.stats(ctx.con)["labelled"] == 2
    seen_first = ls.run(ctx.con, keys("f", "q"), out.append, tmp_path / "a.yaml", now=6.0)       # resumes: the skipped one comes again
    assert seen_first["labelled"] == 1 and phrases.stats(ctx.con)["labelled"] == 3
    assert not any("heard_phrases" in o for o in out)


def test_labeller_shows_her_last_reply_facts_and_the_outcome(ctx, tmp_path):
    on(ctx)
    add(ctx, "and what about tomorrow", since_reply=3.0, followup=True, named=False, outcome="delivered")
    out = []
    _script().run(ctx.con, keys("q"), out.append, tmp_path / "a.yaml", now=1.0)
    text = "\n".join(map(str, out))
    assert "and what about tomorrow" in text and "delivered" in text and "3" in text


def test_labeller_ignores_other_keys_and_ends_on_empty_queue(ctx, tmp_path):
    fill(ctx, 1, 0)
    n = _script().run(ctx.con, keys("x", "", "f"), lambda *_: None, tmp_path / "a.yaml", now=1.0)
    assert n["labelled"] == 1


def test_export_writes_yaml_outside_the_repo_and_counts(ctx, tmp_path):
    fill(ctx, 3, 3)
    for r in phrases.queue(ctx.con, 4):
        phrases.set_label(ctx.con, r["id"], "for_her" if r["outcome"] != "dropped" else "not_for_her", 2.0)
    out = tmp_path / "checks" / "addressed.yaml"
    c = phrases.export(ctx.con, out)
    assert c["total"] == 4 and c["for_her"] == 2 and c["not_for_her"] == 2 and c["bar_met"] is False
    data = yaml.safe_load(out.read_text())
    assert set(data[0]) == {"text", "label", "named", "followup", "since_reply", "outcome"}


def test_bar_needs_150_with_40_each(ctx, tmp_path):
    on(ctx)
    for i in range(150):
        add(ctx, f"phrase number {i} here", outcome="delivered" if i < 45 else "dropped")
    for r in ctx.con.execute("SELECT id, outcome FROM heard_phrases").fetchall():
        phrases.set_label(ctx.con, r["id"], "for_her" if r["outcome"] == "delivered" else "not_for_her", 2.0)
    assert phrases.export(ctx.con, tmp_path / "a.yaml")["bar_met"] is True
    ctx.con.execute("UPDATE heard_phrases SET label='not_for_her' WHERE label='for_her'")
    assert phrases.export(ctx.con, tmp_path / "a.yaml")["bar_met"] is False


def test_script_default_path_is_outside_the_repo():
    src = (Path(phrases.__file__).resolve().parents[3] / "scripts" / "label_phrases.py").read_text()
    assert "Misty" in src and "checks" in src and "addressed.yaml" in src
