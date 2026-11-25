"""The entity registry: entities are rows, pages are renderings, merges are reversible.

Everything here is DB-only and synchronous. It never touches the wiki files; `registry.render` does that.
Ids are permanent and never reused (`en_` + 12 hex). A merged entity keeps its row and points at its survivor."""
from __future__ import annotations

import json
import sqlite3
import uuid

from ..clock import Clock
from ..db import tx
from .names import canonical_score, core_tokens, norm

LIVE = ("candidate", "active")


class RegistryError(Exception):
    pass


class Registry:
    def __init__(self, con: sqlite3.Connection, clock: Clock):
        self.con = con
        self.clock = clock

    # ---- entities

    def new_entity(self, kind: str, name: str, *, origin: str = "compile", status: str = "candidate",
                   path: str | None = None, known: bool = False) -> str:
        eid = "en_" + uuid.uuid4().hex[:12]
        now = self.clock.now()
        with tx(self.con):
            self.con.execute(
                "INSERT INTO entities(id, kind, name, path, status, origin, known, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (eid, kind, name, path, status, origin, int(known), now, now))
            self._alias(eid, kind, name, "name", 0)
        return eid

    def get(self, eid: str) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()

    def survivor(self, eid: str) -> str:
        """Follow `merged_into` to the entity that is still alive (or the last row of the chain)."""
        for _ in range(32):
            r = self.get(eid)
            if r is None or not r["merged_into"]:
                return eid
            eid = r["merged_into"]
        return eid

    def entities(self, kind: str | None = None, statuses: tuple[str, ...] = LIVE) -> list[sqlite3.Row]:
        q = f"SELECT * FROM entities WHERE status IN ({','.join('?' * len(statuses))})"
        args: list = list(statuses)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        return self.con.execute(q + " ORDER BY created_at, id", args).fetchall()

    def set_pending(self, eid: str, flag: bool) -> None:
        self.con.execute("UPDATE entities SET pending=?, updated_at=? WHERE id=?",
                         (int(flag), self.clock.now(), eid))

    def set_path(self, eid: str, path: str | None) -> None:
        self.con.execute("UPDATE entities SET path=?, updated_at=? WHERE id=?", (path, self.clock.now(), eid))

    def set_status(self, eid: str, status: str) -> None:
        self.con.execute("UPDATE entities SET status=?, updated_at=? WHERE id=?", (status, self.clock.now(), eid))

    def rename(self, eid: str, name: str, pinned: bool = False) -> None:
        r = self.get(eid)
        with tx(self.con):
            self.con.execute("UPDATE entities SET name=?, name_pinned=?, updated_at=? WHERE id=?",
                             (name, int(pinned or r["name_pinned"]), self.clock.now(), eid))
            self._alias(eid, r["kind"], name, "user" if pinned else "name", 0)

    def recompute_name(self, eid: str, chat_titles: frozenset[str] = frozenset()) -> None:
        r = self.get(eid)
        if r is None or r["name_pinned"]:
            return
        if r["kind"] == "person":
            # a person is known by their fullest clean name
            best = max(self.aliases(eid), key=lambda a: canonical_score(a["alias"], a["n"], chat_titles), default=None)
        else:
            # a project or topic keeps the spelling people use most (ties keep the current one): the longest alias
            # of a topic is usually a one-off, not its name
            best = max(self.aliases(eid), default=None, key=lambda a: (
                canonical_score(a["alias"], 0, chat_titles)[0], a["n"], a["alias"] == r["name"], a["alias"]))
        if best and best["alias"] != r["name"]:
            self.con.execute("UPDATE entities SET name=?, updated_at=? WHERE id=?",
                             (best["alias"], self.clock.now(), eid))

    # ---- aliases

    def _alias(self, eid: str, kind: str, alias: str, source: str, n: int) -> None:
        key = " ".join(core_tokens(alias, kind)) if kind == "person" else norm(alias)
        if not key:
            return
        row = self.con.execute("SELECT alias FROM entity_aliases WHERE entity_id=? AND alias_norm=?",
                               (eid, key)).fetchone()
        if row is None:
            self.con.execute("INSERT INTO entity_aliases(entity_id, alias, alias_norm, kind, source, n) "
                             "VALUES (?,?,?,?,?,?)", (eid, alias, key, kind, source, n))
            return
        # one alias_norm, several spellings: keep the cleanest one as the shown text (deterministic tie-break)
        keep = max((row["alias"], alias), key=lambda a: (canonical_score(a, 0)[:2], sum(c.isupper() for c in a), a))
        self.con.execute("UPDATE entity_aliases SET alias=?, n = n + ? WHERE entity_id=? AND alias_norm=?",
                         (keep, n, eid, key))

    def add_alias(self, eid: str, alias: str, *, source: str = "compile", n: int = 1) -> None:
        r = self.get(eid)
        if r is not None:
            self._alias(eid, r["kind"], alias, source, n)

    def aliases(self, eid: str) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM entity_aliases WHERE entity_id=? ORDER BY alias_norm", (eid,)).fetchall()

    def alias_tokens(self, eid: str) -> list[tuple[str, ...]]:
        return [tuple(a["alias_norm"].split()) for a in self.aliases(eid)]

    def find_alias(self, kind: str, alias_norm: str) -> list[str]:
        rows = self.con.execute(
            "SELECT a.entity_id FROM entity_aliases a JOIN entities e ON e.id = a.entity_id "
            "WHERE a.kind=? AND a.alias_norm=? AND e.status IN ('candidate','active') ORDER BY e.created_at, e.id",
            (kind, alias_norm)).fetchall()
        return [r[0] for r in rows]

    # ---- handles

    def add_handle(self, eid: str, type_: str, value: str) -> None:
        self.con.execute("INSERT OR IGNORE INTO entity_handles(type, value, entity_id) VALUES (?,?,?)",
                         (type_, value, eid))

    def by_handle(self, type_: str, value: str) -> str | None:
        r = self.con.execute("SELECT entity_id FROM entity_handles WHERE type=? AND value=?",
                             (type_, value)).fetchone()
        return self.survivor(r[0]) if r else None

    def handles(self, eid: str) -> list[tuple[str, str]]:
        return [(r["type"], r["value"]) for r in self.con.execute(
            "SELECT type, value FROM entity_handles WHERE entity_id=? ORDER BY type, value", (eid,))]

    # ---- mentions

    def add_mention(self, item_id: str, eid: str, raw: str, role: str = "named") -> None:
        self.con.execute("INSERT OR IGNORE INTO mentions(item_id, entity_id, raw, role) VALUES (?,?,?,?)",
                         (item_id, eid, raw, role))

    def mention_items(self, eid: str) -> list[str]:
        return [r[0] for r in self.con.execute(
            "SELECT DISTINCT item_id FROM mentions WHERE entity_id=? ORDER BY item_id", (eid,))]

    def mention_count(self, eid: str) -> int:
        return self.con.execute("SELECT COUNT(DISTINCT item_id) FROM mentions WHERE entity_id=?", (eid,)).fetchone()[0]

    # ---- redirects

    def redirect(self, old_path: str) -> str | None:
        r = self.con.execute("SELECT entity_id FROM entity_redirects WHERE old_path=?", (old_path,)).fetchone()
        return self.survivor(r[0]) if r else None

    # ---- merge / unmerge

    def merge(self, keep: str, drop: str, *, reason: str, verdict: str | None = None) -> int:
        if keep == drop:
            raise RegistryError("MERGE_SELF")
        k, d = self.get(keep), self.get(drop)
        if k is None or d is None:
            raise RegistryError("MERGE_UNKNOWN")
        if d["status"] == "active" and k["status"] != "active":
            keep, drop, k, d = drop, keep, d, k           # a page that exists is never the one that disappears
        if k["kind"] != d["kind"]:
            raise RegistryError("MERGE_KIND")
        if k["status"] == "merged" or d["status"] == "merged":
            raise RegistryError("MERGE_ALREADY")
        c = self.con
        now = self.clock.now()
        with tx(c):
            keep_aliases = {r["alias_norm"]: r["n"] for r in self.aliases(keep)}
            drop_aliases = [dict(r) for r in self.aliases(drop)]
            keep_mentions = {(r["item_id"], r["raw"]) for r in c.execute(
                "SELECT item_id, raw FROM mentions WHERE entity_id=?", (keep,))}
            drop_mentions = [dict(r) for r in c.execute("SELECT * FROM mentions WHERE entity_id=?", (drop,))]
            text_k = c.execute("SELECT * FROM entity_text WHERE entity_id=?", (keep,)).fetchone()
            text_d = c.execute("SELECT * FROM entity_text WHERE entity_id=?", (drop,)).fetchone()
            moved_redirects = [r[0] for r in c.execute(
                "SELECT old_path FROM entity_redirects WHERE entity_id=?", (drop,))]
            snap = {
                "drop": dict(d),
                "keep": {x: k[x] for x in ("name", "name_pinned", "known", "importance", "updated_at")},
                "drop_aliases": drop_aliases,
                "keep_alias_n": keep_aliases,
                "drop_handles": [list(h) for h in self.handles(drop)],
                "drop_mentions": [{x: m[x] for x in ("item_id", "raw", "role")} for m in drop_mentions],
                "shared_mentions": [list(t) for t in keep_mentions
                                    if t in {(m["item_id"], m["raw"]) for m in drop_mentions}],
                "keep_text": dict(text_k) if text_k else None,
                "drop_text": dict(text_d) if text_d else None,
                "redirects_moved": moved_redirects,
                "redirect_created": d["path"],
            }
            # aliases
            for a in drop_aliases:
                if a["alias_norm"] in keep_aliases:
                    c.execute("UPDATE entity_aliases SET n = n + ? WHERE entity_id=? AND alias_norm=?",
                              (a["n"], keep, a["alias_norm"]))
                    c.execute("DELETE FROM entity_aliases WHERE entity_id=? AND alias_norm=?",
                              (drop, a["alias_norm"]))
                else:
                    c.execute("UPDATE entity_aliases SET entity_id=? WHERE entity_id=? AND alias_norm=?",
                              (keep, drop, a["alias_norm"]))
            c.execute("UPDATE entity_handles SET entity_id=? WHERE entity_id=?", (keep, drop))
            c.execute("UPDATE OR IGNORE mentions SET entity_id=? WHERE entity_id=?", (keep, drop))
            c.execute("DELETE FROM mentions WHERE entity_id=?", (drop,))
            if text_d is not None:
                if text_k is None or not (text_k["summary"] or "").strip():
                    c.execute("DELETE FROM entity_text WHERE entity_id=?", (keep,))
                    c.execute("UPDATE entity_text SET entity_id=? WHERE entity_id=?", (keep, drop))
                else:
                    c.execute("DELETE FROM entity_text WHERE entity_id=?", (drop,))
            if d["path"]:
                c.execute("INSERT OR REPLACE INTO entity_redirects(old_path, entity_id) VALUES (?,?)",
                          (d["path"], keep))
            c.execute("UPDATE entity_redirects SET entity_id=? WHERE entity_id=?", (keep, drop))
            c.execute("UPDATE entities SET status='merged', merged_into=?, path=NULL, pending=0, updated_at=? "
                      "WHERE id=?", (keep, now, drop))
            c.execute("DELETE FROM entity_vec WHERE entity_id IN (?,?)", (drop, keep))   # keep gained spellings
            # the survivor
            if d["name_pinned"] and not k["name_pinned"]:
                c.execute("UPDATE entities SET name=?, name_pinned=1 WHERE id=?", (d["name"], keep))
            else:
                self.recompute_name(keep)
            c.execute("UPDATE entities SET known=?, importance=?, updated_at=? WHERE id=?",
                      (max(k["known"], d["known"]), max(k["importance"], d["importance"]), now, keep))
            c.execute("UPDATE entity_text SET prompt_v=0 WHERE entity_id=?", (keep,))
            cur = c.execute(
                "INSERT INTO entity_merges(ts, kept, dropped, reason, verdict, snapshot) VALUES (?,?,?,?,?,?)",
                (now, keep, drop, reason, verdict, json.dumps(snap)))
            return int(cur.lastrowid)

    def unmerge(self, merge_id: int) -> tuple[str, str]:
        row = self.con.execute("SELECT * FROM entity_merges WHERE id=?", (merge_id,)).fetchone()
        if row is None:
            raise RegistryError("UNMERGE_UNKNOWN")
        if row["undone"]:
            raise RegistryError("UNMERGE_DONE")
        s = json.loads(row["snapshot"])
        keep, drop = row["kept"], row["dropped"]
        c = self.con
        d = s["drop"]
        with tx(c):
            # aliases
            before = s["keep_alias_n"]
            for a in s["drop_aliases"]:
                if a["alias_norm"] in before:
                    c.execute("UPDATE entity_aliases SET n = MAX(n - ?, 0) WHERE entity_id=? AND alias_norm=?",
                              (a["n"], keep, a["alias_norm"]))
                else:
                    c.execute("DELETE FROM entity_aliases WHERE entity_id=? AND alias_norm=?",
                              (keep, a["alias_norm"]))
                c.execute("INSERT OR REPLACE INTO entity_aliases(entity_id, alias, alias_norm, kind, source, n) "
                          "VALUES (?,?,?,?,?,?)",
                          (drop, a["alias"], a["alias_norm"], a["kind"], a["source"], a["n"]))
            for t, v in s["drop_handles"]:
                c.execute("UPDATE entity_handles SET entity_id=? WHERE type=? AND value=?", (drop, t, v))
            shared = {tuple(x) for x in s["shared_mentions"]}
            for m in s["drop_mentions"]:
                if (m["item_id"], m["raw"]) not in shared:
                    c.execute("DELETE FROM mentions WHERE item_id=? AND entity_id=? AND raw=?",
                              (m["item_id"], keep, m["raw"]))
                c.execute("INSERT OR IGNORE INTO mentions(item_id, entity_id, raw, role) VALUES (?,?,?,?)",
                          (m["item_id"], drop, m["raw"], m["role"]))
            # text
            c.execute("DELETE FROM entity_text WHERE entity_id IN (?,?)", (keep, drop))
            for ent, t in ((keep, s["keep_text"]), (drop, s["drop_text"])):
                if t:
                    cols = list(t)
                    c.execute(f"INSERT INTO entity_text({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                              [t[x] for x in cols])
            # redirects
            if s["redirect_created"]:
                c.execute("DELETE FROM entity_redirects WHERE old_path=?", (s["redirect_created"],))
            for p in s["redirects_moved"]:
                c.execute("UPDATE entity_redirects SET entity_id=? WHERE old_path=?", (drop, p))
            # the two rows
            path = d["path"]
            if path and c.execute("SELECT 1 FROM entities WHERE path=? AND status='active' AND id!=?",
                                  (path, drop)).fetchone():
                path = None
            c.execute("UPDATE entities SET status=?, merged_into=NULL, path=?, pending=?, updated_at=? WHERE id=?",
                      (d["status"], path, d["pending"], d["updated_at"], drop))
            kk = s["keep"]
            c.execute("UPDATE entities SET name=?, name_pinned=?, known=?, importance=?, updated_at=? WHERE id=?",
                      (kk["name"], kk["name_pinned"], kk["known"], kk["importance"], kk["updated_at"], keep))
            c.execute("UPDATE entity_merges SET undone=1 WHERE id=?", (merge_id,))
        return keep, drop
