# sqlite-vec + FTS5 (M0 Task 0.5, checked 2026-10-14)

Command: `uv init --python 3.12 --bare vt && uv add sqlite-vec`, then load into an in-memory sqlite3 connection.

- sqlite-vec package 0.1.9 (`vec_version()` = v0.1.9) loads via `conn.enable_load_extension(True); sqlite_vec.load(conn)` on uv's standalone Python 3.12. `enable_load_extension` exists, so no pysqlite3/apsw fallback needed.
- SQLite 3.50.4. FTS5 is compiled in; `tokenize='porter unicode61 remove_diacritics 2'` works ('run' matches 'running').
- `vec0(embedding float[N] distance_metric=cosine)` KNN query `where embedding match ? and k = 1` returns (rowid, distance=0.0) for an identical vector.
- Insert vectors with `sqlite_vec.serialize_float32(list)`.
- Caveat for M6: the bundled standalone Python must also allow extension loading (check with the same snippet inside Misty.app/Contents/Resources/engine/python).
