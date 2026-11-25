import sqlite3

import numpy as np
import pytest
import sqlite_vec

from misty_engine.db import migrate, tx


def test_migrate_is_idempotent(con):
    migrate(con, 768)
    migrate(con, 768)
    assert con.execute("select count(*) from schema_version").fetchone()[0] == 39     # v1 schema + v2, v6, v7, v8, v9, v10, v11, v12, v13, v14, v15, v16, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29, v30, v31, v32, v33, v34, v35, v36, v37, v38, v39, v40, v41, v42 migrations


def test_wal_and_fk_on(con):
    assert con.execute("pragma journal_mode").fetchone()[0] == "wal"
    assert con.execute("pragma foreign_keys").fetchone()[0] == 1


def test_item_status_check_rejects_unknown(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("insert into items(id,source,kind,locator,status,updated_at) values('it_1','x','file','/a','bogus',0)")


def test_vec0_cosine_roundtrip(con):
    v = np.zeros(768, dtype=np.float32)
    v[0] = 1
    blob = sqlite_vec.serialize_float32(v.tolist())
    con.execute("insert into vectors(rowid, embedding, kind, source) values (1, ?, 'chunk', 'folders')", (blob,))
    row = con.execute("select rowid, distance from vectors where embedding match ? and k = 1", (blob,)).fetchone()
    assert row[0] == 1 and abs(row[1]) < 1e-6


def test_migrate_rebuilds_m1_vectors_table(misty_home):
    from misty_engine.db import connect, migrate
    c = connect(misty_home.home / "old.db")
    c.executescript(open(__import__("misty_engine.db", fromlist=["x"]).SCHEMA).read())
    c.execute("create virtual table vectors using vec0(embedding float[768] distance_metric=cosine)")
    c.execute("insert into vec_refs(ref_kind, ref_id) values ('chunk', 'a:0')")
    migrate(c, 768)
    assert "kind text" in c.execute("select sql from sqlite_master where name='vectors'").fetchone()[0]
    assert c.execute("select count(*) from vec_refs").fetchone()[0] == 0
    c.close()


def test_fts5_porter(con):
    con.execute("insert into fts(text, ref_kind, ref_id) values ('running quickly', 'chunk', 'it_1:0')")
    assert con.execute("select count(*) from fts where fts match 'run'").fetchone()[0] == 1


def test_settings_defaults_and_set(settings):
    assert settings.get("cap_daily_usd") == 0.20
    settings.set("paused", True)
    assert settings.get("paused") is True
    with pytest.raises(KeyError):
        settings.set("nope", 1)


def test_tx_rolls_back_on_error(con):
    with pytest.raises(RuntimeError):
        with tx(con):
            con.execute("insert into settings(key, value) values ('k','1')")
            raise RuntimeError("boom")
    assert con.execute("select count(*) from settings").fetchone()[0] == 0


def test_v2_adds_spend_purpose_and_is_applied_once(con):
    cols = [r[1] for r in con.execute("pragma table_info(spend)")]
    assert "purpose" in cols
    assert [r[0] for r in con.execute("select version from schema_version order by version")] == [1, 2, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42]


def test_registry_tables_exist(con):
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"entities", "entity_aliases", "entity_handles", "mentions", "entity_merges", "judgements",
            "entity_text", "entity_redirects", "entity_vec"} <= names
    cols = {r[1] for r in con.execute("PRAGMA table_info(spend)")}
    assert "purpose" in cols
