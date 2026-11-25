"""FTS5 keyword index and sqlite-vec vector index, kept in step.

fts rowids: chunks use item_chunks.rowid (positive); pages use -vec_refs.rowid (negative), so they never collide.
vectors rowid = vec_refs.rowid; vec_refs says what each vector is.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import sqlite_vec

from .db import tx


@dataclass(frozen=True)
class VecHit:
    ref_kind: str
    ref_id: str
    item_id: str | None
    sim: float


@dataclass(frozen=True)
class FtsHit:
    ref_kind: str
    ref_id: str
    rank: float
    snippet: str


def fts_query(text: str) -> str | None:
    """Any user text becomes a safe FTS5 query: quoted word tokens joined by OR. None if there are no words."""
    toks = re.findall(r"\w+", text.lower())[:16]
    return " OR ".join(f'"{t}"' for t in toks) if toks else None


def _blob(v) -> bytes:
    return sqlite_vec.serialize_float32(np.asarray(v, dtype=np.float32).tolist())


def _marks(n: int) -> str:
    return ",".join("?" * n)


class Index:
    def __init__(self, con: sqlite3.Connection, dim: int):
        self.con, self.dim = con, dim

    # -- vector rows
    def _put_vec(self, ref_kind: str, ref_id: str, item_id: str | None, source: str | None, vec) -> int:
        r = self.con.execute("SELECT rowid FROM vec_refs WHERE ref_kind=? AND ref_id=?", (ref_kind, ref_id)).fetchone()
        if r:
            rowid = r[0]
            self.con.execute("DELETE FROM vectors WHERE rowid=?", (rowid,))
            self.con.execute("UPDATE vec_refs SET item_id=?, source=? WHERE rowid=?", (item_id, source, rowid))
        else:
            rowid = self.con.execute(
                "INSERT INTO vec_refs(ref_kind, ref_id, item_id, source) VALUES (?,?,?,?)",
                (ref_kind, ref_id, item_id, source)).lastrowid
        self.con.execute("INSERT INTO vectors(rowid, embedding, kind, source) VALUES (?,?,?,?)",
                         (rowid, _blob(vec), ref_kind, source or ""))
        return rowid

    def _drop_vecs(self, where: str, args: tuple) -> None:
        rows = [r[0] for r in self.con.execute(f"SELECT rowid FROM vec_refs WHERE {where}", args)]
        for rid in rows:
            self.con.execute("DELETE FROM vectors WHERE rowid=?", (rid,))
        self.con.execute(f"DELETE FROM vec_refs WHERE {where}", args)

    def _drop_chunks(self, item_id: str) -> None:
        self.con.execute("DELETE FROM fts WHERE rowid IN (SELECT rowid FROM item_chunks WHERE item_id=?)", (item_id,))
        self.con.execute("DELETE FROM item_chunks WHERE item_id=?", (item_id,))
        self._drop_vecs("item_id=? AND ref_kind='chunk'", (item_id,))

    # -- writes
    def replace_item_chunks(self, item_id: str, source: str, chunks: Sequence[str], vectors: np.ndarray) -> None:
        assert len(chunks) == len(vectors)
        with tx(self.con):
            self._drop_chunks(item_id)
            for n, (text, vec) in enumerate(zip(chunks, vectors)):
                rid = self.con.execute(
                    "INSERT INTO item_chunks(item_id, chunk_no, text_scrubbed) VALUES (?,?,?)",
                    (item_id, n, text)).lastrowid
                self.con.execute("INSERT INTO fts(rowid, text, ref_kind, ref_id) VALUES (?,?,?,?)",
                                 (rid, text, "chunk", f"{item_id}:{n}"))
                self._put_vec("chunk", f"{item_id}:{n}", item_id, source, vec)

    def set_item_vector(self, item_id: str, source: str, vec) -> None:
        with tx(self.con):
            self._put_vec("item", item_id, item_id, source, vec)

    def set_image(self, item_id: str, source: str, vec) -> None:
        with tx(self.con):
            self._put_vec("image", item_id, item_id, source, vec)

    def upsert_page(self, path: str, text: str, vec) -> None:
        with tx(self.con):
            rid = self._put_vec("page", path, None, "wiki", vec)
            self.con.execute("DELETE FROM fts WHERE rowid=?", (-rid,))
            self.con.execute("INSERT INTO fts(rowid, text, ref_kind, ref_id) VALUES (?,?,?,?)",
                             (-rid, text, "page", path))

    def page_vectors(self, paths: Sequence[str]) -> dict[str, np.ndarray]:
        """Stored embeddings for wiki pages, by path (used to discover life areas)."""
        out: dict[str, np.ndarray] = {}
        paths = list(paths)
        for i in range(0, len(paths), 500):
            chunk = paths[i:i + 500]
            rows = self.con.execute(
                f"SELECT r.ref_id, v.embedding FROM vec_refs r JOIN vectors v ON v.rowid = r.rowid "
                f"WHERE r.ref_kind='page' AND r.ref_id IN ({_marks(len(chunk))})", chunk).fetchall()
            for ref_id, blob in rows:
                out[ref_id] = np.frombuffer(blob, dtype=np.float32).copy()
        return out

    def remove_page(self, path: str) -> None:
        with tx(self.con):
            r = self.con.execute("SELECT rowid FROM vec_refs WHERE ref_kind='page' AND ref_id=?", (path,)).fetchone()
            if r:
                self.con.execute("DELETE FROM fts WHERE rowid=?", (-r[0],))
            self._drop_vecs("ref_kind='page' AND ref_id=?", (path,))

    def remove_item(self, item_id: str) -> None:
        with tx(self.con):
            self._drop_chunks(item_id)
            self._drop_vecs("item_id=?", (item_id,))

    # -- reads
    def _knn(self, qvec, k: int, kinds: Sequence[str], source: str | None = None) -> list[tuple[int, float]]:
        sql = f"SELECT rowid, distance FROM vectors WHERE embedding MATCH ? AND k=? AND kind IN ({_marks(len(kinds))})"
        args: list = [_blob(qvec), k, *kinds]
        if source is not None:
            sql += " AND source=?"
            args.append(source)
        return [(r[0], r[1]) for r in self.con.execute(sql, args)]

    def near_duplicate(self, item_id: str, source: str, vec, threshold: float = 0.97) -> str | None:
        for rowid, dist in self._knn(vec, 5, ("item",), source):
            if 1.0 - dist <= threshold:
                continue
            r = self.con.execute("SELECT item_id FROM vec_refs WHERE rowid=?", (rowid,)).fetchone()
            if r and r[0] != item_id:
                return r[0]
        return None

    def vector_search(self, qvec, k: int = 200, ref_kinds: Sequence[str] = ("chunk", "image")) -> list[VecHit]:
        hits = self._knn(qvec, k, ref_kinds)
        if not hits:
            return []
        refs = {r["rowid"]: r for r in self.con.execute(
            f"SELECT rowid, ref_kind, ref_id, item_id FROM vec_refs WHERE rowid IN ({_marks(len(hits))})",
            [h[0] for h in hits])}
        return [VecHit(refs[rid]["ref_kind"], refs[rid]["ref_id"], refs[rid]["item_id"], 1.0 - dist)
                for rid, dist in hits if rid in refs]

    def fts_search(self, text: str, k: int = 200, ref_kinds: Sequence[str] = ("chunk",)) -> list[FtsHit]:
        q = fts_query(text)
        if q is None:
            return []
        try:
            rows = self.con.execute(
                f"SELECT ref_kind, ref_id, bm25(fts) AS rank, snippet(fts, 0, '«', '»', '…', 12) AS snip "
                f"FROM fts WHERE fts MATCH ? AND ref_kind IN ({_marks(len(ref_kinds))}) ORDER BY rank LIMIT ?",
                [q, *ref_kinds, k]).fetchall()
        except sqlite3.OperationalError:
            return []
        return [FtsHit(r["ref_kind"], r["ref_id"], r["rank"], r["snip"]) for r in rows]
