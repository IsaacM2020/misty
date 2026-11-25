"""SQLite job queue with the retry state machine from spec section 10."""
from __future__ import annotations

import json
from typing import Sequence

from .clock import Clock
from .config import Settings
from .db import tx
from .errors import CapHit, Fatal, MistyError, NetworkDown, Retryable
from .logs import log_event
from .types import Job, new_id

BACKOFF_S = (5, 30, 120)
MAX_RETRIES = 3
ACTIVE_WAITING = ("queued", "running", "waiting_network", "waiting_cap")


class JobQueue:
    def __init__(self, con, clock: Clock, settings: Settings):
        self.con, self.clock, self.settings = con, clock, settings

    # -- helpers
    def _row_to_job(self, r) -> Job:
        return Job(id=r["id"], kind=r["kind"], item_id=r["item_id"], payload=json.loads(r["payload"]),
                   cloud=bool(r["cloud"]), status=r["status"], attempts=r["attempts"],
                   next_run_at=r["next_run_at"], error_code=r["error_code"])

    def get(self, job_id: str) -> Job:
        r = self.con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if r is None:
            raise KeyError(job_id)
        return self._row_to_job(r)

    # -- producing
    def enqueue(self, kind: str, item_id: str | None = None, payload: dict | None = None, *,
                cloud: bool = False, run_at: float | None = None) -> str:
        now = self.clock.now()
        with tx(self.con):
            if item_id is not None:
                r = self.con.execute(
                    "SELECT id FROM jobs WHERE kind=? AND item_id=? AND status NOT IN ('done','failed')",
                    (kind, item_id)).fetchone()
                if r:
                    return r["id"]
            else:                                   # the same job already waiting adds nothing (e.g. a rescan per engine start)
                r = self.con.execute("SELECT id FROM jobs WHERE kind=? AND item_id IS NULL AND payload=? AND status='queued'",
                                     (kind, json.dumps(payload or {}))).fetchone()
                if r:
                    return r["id"]
            jid = new_id("jb")
            self.con.execute(
                "INSERT INTO jobs(id, kind, item_id, payload, cloud, status, attempts, next_run_at, created_at, updated_at) "
                "VALUES (?,?,?,?,?, 'queued', 0, ?, ?, ?)",
                (jid, kind, item_id, json.dumps(payload or {}), int(cloud), run_at if run_at is not None else now, now, now))
        return jid

    def enqueue_paths(self, kind: str, paths: list[str]) -> str:
        """Changed files join the one batch already waiting instead of each save adding a job."""
        with tx(self.con):
            r = self.con.execute("SELECT id, payload FROM jobs WHERE kind=? AND item_id IS NULL AND status='queued' "
                                 "ORDER BY created_at LIMIT 1", (kind,)).fetchone()
            if r:
                have = json.loads(r["payload"]).get("paths", [])
                merged = have + [p for p in paths if p not in have]
                self.con.execute("UPDATE jobs SET payload=?, updated_at=? WHERE id=?",
                                 (json.dumps({"paths": merged}), self.clock.now(), r["id"]))
                return r["id"]
        return self.enqueue(kind, payload={"paths": paths})

    # -- consuming
    def claim(self, kinds: Sequence[str], limit: int) -> list[Job]:
        if self.settings.get("paused") or not kinds or limit <= 0:
            return []
        now = self.clock.now()
        marks = ",".join("?" * len(kinds))
        with tx(self.con):
            rows = self.con.execute(
                f"SELECT * FROM jobs WHERE status='queued' AND next_run_at<=? AND kind IN ({marks}) "
                f"ORDER BY next_run_at, created_at LIMIT ?", (now, *kinds, limit)).fetchall()
            for r in rows:
                self.con.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?", (now, r["id"]))
        out = []
        for r in rows:
            j = self._row_to_job(r)
            j.status = "running"
            out.append(j)
        return out

    def complete(self, job_id: str) -> None:
        self.con.execute("UPDATE jobs SET status='done', error_code=NULL, updated_at=? WHERE id=?",
                         (self.clock.now(), job_id))

    def fail(self, job_id: str, err: MistyError) -> str:
        now = self.clock.now()
        j = self.get(job_id)
        attempts, next_run = j.attempts, now
        if isinstance(err, NetworkDown):
            status = "waiting_network"
        elif isinstance(err, CapHit):
            status = "waiting_cap"
        elif isinstance(err, Retryable):
            attempts += 1
            if attempts > MAX_RETRIES:
                status = "failed"
            else:
                status, next_run = "queued", now + BACKOFF_S[attempts - 1]
        else:  # Fatal or anything else
            status = "failed"
        self.con.execute(
            "UPDATE jobs SET status=?, attempts=?, next_run_at=?, error_code=?, updated_at=? WHERE id=?",
            (status, attempts, next_run, err.code, now, job_id))
        log_event("JOB_FAIL", job_id=job_id, kind=j.kind, status=status, code=err.code, attempt=attempts)
        return status

    def retry(self, job_id: str) -> None:
        now = self.clock.now()
        self.con.execute(
            "UPDATE jobs SET status='queued', attempts=0, error_code=NULL, next_run_at=?, updated_at=? "
            "WHERE id=? AND status='failed'", (now, now, job_id))

    def release(self, status: str) -> int:
        """Move every job in a waiting_* status back to queued (network back, cap raised, midnight)."""
        assert status in ("waiting_network", "waiting_cap")
        now = self.clock.now()
        cur = self.con.execute(
            "UPDATE jobs SET status='queued', next_run_at=?, updated_at=? WHERE status=?", (now, now, status))
        return cur.rowcount

    def release_code(self, code: str) -> int:
        """Move the waiting_cap jobs that were stopped by one cap (error_code like CAP_PURPOSE:judge) back to queued."""
        now = self.clock.now()
        cur = self.con.execute(
            "UPDATE jobs SET status='queued', next_run_at=?, updated_at=? WHERE status='waiting_cap' AND error_code=?",
            (now, now, code))
        return cur.rowcount

    def recover_running(self) -> int:
        """At start: what was running is queued again. `waiting_quota` is a leftover of the old subscription
        runner (still allowed by the table's CHECK, never produced now); such a row is just queued work."""
        now = self.clock.now()
        cur = self.con.execute(
            "UPDATE jobs SET status='queued', next_run_at=?, updated_at=? "
            "WHERE status IN ('running','waiting_quota')", (now, now))
        return cur.rowcount

    # -- reporting
    def counts(self) -> dict[str, int]:
        rows = self.con.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def pending(self, kinds: Sequence[str] | None = None) -> int:
        marks = ",".join("?" * len(ACTIVE_WAITING))
        sql = f"SELECT COUNT(*) FROM jobs WHERE status IN ({marks})"
        args: list = list(ACTIVE_WAITING)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += list(kinds)
        return self.con.execute(sql, args).fetchone()[0]
