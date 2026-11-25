import json
import sqlite3

import pytest

from misty_engine.errors import SchemaUnrecognized
from misty_engine.sources.claude_mem import ClaudeMemSource


@pytest.fixture
def mem_db(tmp_path):
    p = tmp_path / "claude-mem.db"
    c = sqlite3.connect(p)
    c.execute("""create table observations (id integer primary key, project text, type text, title text,
                 subtitle text, narrative text, facts text, concepts text, created_at text)""")
    c.execute("""create table session_summaries (id integer primary key, project text, request text,
                 investigated text, learned text, completed text, next_steps text, notes text, created_at text)""")
    for i in (1, 2, 3):
        c.execute("insert into observations values (?,?,?,?,?,?,?,?,?)",
                  (i, "Notes", "feature", f"Obs title {i}" if i != 3 else None, "sub" if i == 3 else None,
                   f"narrative number {i} about iGEM wiki", '["fact a","fact b"]', "synbio, wiki",
                   f"2026-10-1{i}T04:28:40.930Z"))
        c.execute("insert into session_summaries values (?,?,?,?,?,?,?,?,?)",
                  (i, "Notes", f"Request {i}: build the thing " + "x" * 100, "looked at files", "learned Y",
                   "completed Z", "next steps here", None, f"2026-10-1{i}T07:38:45.885Z"))
    c.commit()
    c.close()
    return p


def test_discover_counts_and_shapes(mem_db):
    src = ClaudeMemSource(mem_db)
    items = list(src.discover(None))
    assert len(items) == 6
    obs = [i for i in items if i.kind == "mem_obs"]
    sums = [i for i in items if i.kind == "mem_summary"]
    assert len(obs) == len(sums) == 3
    assert {i.locator for i in obs} == {"claude_mem:obs:1", "claude_mem:obs:2", "claude_mem:obs:3"}
    assert next(i for i in obs if i.locator.endswith(":3")).title == "sub"      # title falls back to subtitle
    assert all(len(i.title) <= 80 for i in sums)
    assert all(i.source == "claude_mem" for i in items)
    assert obs[0].created_at == "2026-10-11T04:28:40Z"                          # same format as file items


def test_cursor_advances_and_second_run_is_empty(mem_db):
    src = ClaudeMemSource(mem_db)
    list(src.discover(None))
    cur = src.next_cursor()
    assert json.loads(cur) == {"obs": 3, "sum": 3}
    assert list(ClaudeMemSource(mem_db).discover(cur)) == []
    c = sqlite3.connect(mem_db)
    c.execute("insert into observations values (4,'p','t','New one',null,'n',null,null,'2026-10-14T00:00:00.000Z')")
    c.commit()
    c.close()
    again = ClaudeMemSource(mem_db)
    new = list(again.discover(cur))
    assert [i.locator for i in new] == ["claude_mem:obs:4"]


def test_limit_only_advances_cursor_over_yielded_rows(mem_db):
    src = ClaudeMemSource(mem_db)
    got = list(src.discover(None, limit=2))
    assert len(got) == 2
    cur = json.loads(src.next_cursor())
    rest = list(ClaudeMemSource(mem_db).discover(json.dumps(cur)))
    assert len(got) + len(rest) == 6


def test_load_text_has_narrative_and_labels(mem_db):
    src = ClaudeMemSource(mem_db)
    items = {i.locator: i for i in src.discover(None)}
    t = src.load_text(items["claude_mem:obs:1"])
    assert "narrative number 1 about iGEM wiki" in t and "fact a" in t and "Obs title 1" in t
    s = src.load_text(items["claude_mem:sum:2"])
    assert "Request 2" in s and "learned Y" in s and "Next steps: next steps here" in s


def test_opened_read_only(mem_db):
    src = ClaudeMemSource(mem_db)
    list(src.discover(None))
    c = sqlite3.connect(mem_db)
    assert c.execute("select count(*) from observations").fetchone()[0] == 3     # untouched
    c.close()
    with pytest.raises(sqlite3.OperationalError):
        src._connect().execute("insert into observations(id) values (99)")


def test_missing_column_is_schema_unrecognized(tmp_path):
    p = tmp_path / "bad.db"
    c = sqlite3.connect(p)
    c.execute("create table observations (id integer primary key, project text)")
    c.execute("create table session_summaries (id integer primary key, request text)")
    c.commit()
    c.close()
    with pytest.raises(SchemaUnrecognized):
        ClaudeMemSource(p).check_schema()


def test_missing_db_yields_nothing(tmp_path):
    src = ClaudeMemSource(tmp_path / "nope.db")
    assert list(src.discover(None)) == [] and src.stats["missing"] == 1


async def test_claude_mem_through_pipeline_needs_no_llm(ctx, mem_db):
    from misty_engine.pipeline import Worker
    ctx.sources = {"claude_mem": ClaudeMemSource(mem_db)}
    ctx.queue.enqueue("discover", payload={"source": "claude_mem"})
    await Worker(ctx, {"discover": 1, "prep": 4}).run_until_idle()
    assert dict(ctx.con.execute("select status, count(*) from items group by status").fetchall()) == {"done": 6}
    assert ctx.index.fts_search("wiki")           # indexed and searchable
    assert ctx.con.execute("select health from sources_state where source='claude_mem'").fetchone()[0] == "ok"
