"""SQLite connection, schema migration and a tiny transaction helper."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import sqlite_vec

SCHEMA = Path(__file__).with_name("schema.sql")
MIGRATIONS: dict[int, str] = {  # version -> sql, applied in order after v1
    2: Path(__file__).with_name("migration_v2.sql").read_text(),
    6: Path(__file__).with_name("migration_v6.sql").read_text(),   # 3-5 were hands-v2 (dropped), already on the live db
    7: Path(__file__).with_name("migration_v7.sql").read_text(),
    8: Path(__file__).with_name("migration_v8.sql").read_text(),
    9: Path(__file__).with_name("migration_v9.sql").read_text(),

    10: Path(__file__).with_name("migration_v10.sql").read_text(),
    11: Path(__file__).with_name("migration_v11.sql").read_text(),
    12: Path(__file__).with_name("migration_v12.sql").read_text(),
    13: Path(__file__).with_name("migration_v13.sql").read_text(),
    14: Path(__file__).with_name("migration_v14.sql").read_text(),
    15: Path(__file__).with_name("migration_v15.sql").read_text(),
    16: Path(__file__).with_name("migration_v16.sql").read_text(),
    17: Path(__file__).with_name("migration_v17.sql").read_text(),
    18: Path(__file__).with_name("migration_v18.sql").read_text(),
    19: Path(__file__).with_name("migration_v19.sql").read_text(),
    20: Path(__file__).with_name("migration_v20.sql").read_text(),
    21: Path(__file__).with_name("migration_v21.sql").read_text(),
    22: Path(__file__).with_name("migration_v22.sql").read_text(),
    23: Path(__file__).with_name("migration_v23.sql").read_text(),
    24: Path(__file__).with_name("migration_v24.sql").read_text(),
    25: Path(__file__).with_name("migration_v25.sql").read_text(),
    26: Path(__file__).with_name("migration_v26.sql").read_text(),
    27: Path(__file__).with_name("migration_v27.sql").read_text(),
    28: Path(__file__).with_name("migration_v28.sql").read_text(),
    29: Path(__file__).with_name("migration_v29.sql").read_text(),
    30: Path(__file__).with_name("migration_v30.sql").read_text(),
    31: Path(__file__).with_name("migration_v31.sql").read_text(),
    32: Path(__file__).with_name("migration_v32.sql").read_text(),
    33: Path(__file__).with_name("migration_v33.sql").read_text(),
    34: Path(__file__).with_name("migration_v34.sql").read_text(),
    35: Path(__file__).with_name("migration_v35.sql").read_text(),
    36: Path(__file__).with_name("migration_v36.sql").read_text(),
    37: Path(__file__).with_name("migration_v37.sql").read_text(),
    38: Path(__file__).with_name("migration_v38.sql").read_text(),
    39: Path(__file__).with_name("migration_v39.sql").read_text(),
    40: Path(__file__).with_name("migration_v40.sql").read_text(),
    41: Path(__file__).with_name("migration_v41.sql").read_text(),
    42: Path(__file__).with_name("migration_v42.sql").read_text(),
}


def connect(path: Path | str, *, load_vec: bool = True) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA synchronous=NORMAL")
    if load_vec:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
    return con


def _vectors_sql(dim: int) -> str:
    # kind/source are vec0 metadata columns so KNN can filter in the index (no over-fetching)
    return (f"CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING vec0("
            f"embedding float[{int(dim)}] distance_metric=cosine, kind text, source text)")


def migrate(con: sqlite3.Connection, dim: int) -> None:
    con.executescript(SCHEMA.read_text())
    old = con.execute("SELECT sql FROM sqlite_master WHERE name='vectors'").fetchone()
    if old is not None and "kind text" not in old[0]:
        # M1 shape (no metadata columns). Nothing was indexed under it, so rebuild empty.
        con.execute("DROP TABLE vectors")
        con.execute("DELETE FROM vec_refs")
    con.execute(_vectors_sql(dim))
    row = con.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current = row[0] or 0
    if current == 0:
        con.execute("INSERT INTO schema_version(version) VALUES (1)")
        current = 1
    for v in sorted(MIGRATIONS):
        if v > current:
            con.executescript(MIGRATIONS[v])
            con.execute("INSERT INTO schema_version(version) VALUES (?)", (v,))
            current = v


@contextmanager
def tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT. Re-entrant: joins an outer transaction if there is one."""
    if con.in_transaction:
        yield con
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")
