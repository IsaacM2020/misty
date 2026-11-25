"""Gmail reader. Read-only: only getProfile, threads.list, threads.get, history.list are ever called."""
from __future__ import annotations

import base64
import json
import random
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Callable, Iterator

from googleapiclient.errors import HttpError

from ..clock import Clock
from ..errors import Fatal, NetworkDown, Retryable
from ..types import RawItem, approx_tokens
from .google_auth import ALLOWED_ACCOUNT
from .mailclean import clean_mail
from .mailfilter import DEFAULT_ALLOW_DOMAINS, KEEP_RULES, SKIP_RULES, Verdict, classify

RESYNC_DAYS = 30
BATCH = 25                 # threads.get calls per HTTP batch (Google advises staying well under 50)
PACE_S = 6.0               # minimum seconds per batch: the project quota is "units per minute per user"
LIMIT_WAIT_S = 65.0        # a limit window is one minute, so wait it out
FLUSH_EVERY = 250
LOAD_GAP_S = 0.5           # minimum seconds between full-thread fetches (prep runs several workers at once)
RATE_REASONS = {"ratelimitexceeded", "userratelimitexceeded", "quotaexceeded", "dailylimitexceeded",
                "backenderror"}
METADATA_HEADERS = ["Subject", "From", "To", "Cc", "List-Unsubscribe", "Precedence", "Auto-Submitted"]
RULES_VERSION = 2          # bump when a rule changes meaning; see VerdictCache.get


def _headers(msg: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in (msg.get("payload", {}).get("headers") or [])}


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _walk(part: dict, plain: list[str], html: list[str]) -> None:
    mime = part.get("mimeType", "")
    data = (part.get("body") or {}).get("data")
    if data and mime == "text/plain":
        plain.append(_decode(data))
    elif data and mime == "text/html":
        html.append(_decode(data))
    for sub in part.get("parts") or []:
        _walk(sub, plain, html)


def message_text(msg: dict) -> str:
    plain: list[str] = []
    html: list[str] = []
    _walk(msg.get("payload", {}), plain, html)
    if plain:
        return clean_mail("\n".join(plain), is_html=False)
    if html:
        return clean_mail("\n".join(html), is_html=True)
    return ""


def _http_status(e: HttpError) -> int:
    return int(getattr(e.resp, "status", 0) or 0)


def _rate_limited(e: HttpError) -> bool:
    """Gmail answers a rate limit with 403 (or 429), so the reason decides, not the status."""
    s = _http_status(e)
    if s == 429 or s >= 500:
        return True
    if s != 403:
        return False
    try:
        errs = json.loads(e.content or b"{}").get("error", {}).get("errors", [])
    except ValueError:
        return False
    return any(str(x.get("reason", "")).lower() in RATE_REASONS for x in errs)


def _wrap(fn):
    try:
        return fn()
    except HttpError as e:
        if _rate_limited(e):
            raise Retryable(f"GMAIL_{_http_status(e)}")
        if _http_status(e) in (401, 403):
            raise Fatal("NEEDS_AUTH")
        raise


class VerdictCache:
    """Remembers each thread's pre-filter verdict (and, for kept threads, its item fields) between runs.

    Keyed by thread id and Gmail historyId, so a thread that changed is looked at again. Holds thread ids, rule
    names, subjects and addresses, in one local file; never anything that leaves the machine. Without it the
    40 minutes of metadata fetching a slow quota allows would be lost on every failure."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict] = {}
        self._dirty = 0
        if path and path.is_file():
            try:
                self.data = json.loads(path.read_text())
            except (OSError, ValueError):
                self.data = {}

    def get(self, tid: str, hid: str | None) -> dict | None:
        e = self.data.get(tid)
        if not e or not hid or e.get("h") != hid:
            return None
        if e.get("r") == "replied" and e.get("v", 1) < RULES_VERSION:
            return None                    # "replied" used to include mail sent to myself: look again
        return e

    def put(self, tid: str, hid: str, rule: str, keep: bool, item: dict | None) -> None:
        if not hid:
            return
        self.data[tid] = {"h": hid, "r": rule, "k": keep, "i": item, "v": RULES_VERSION}
        self._dirty += 1
        if self._dirty >= FLUSH_EVERY:
            self.flush()

    def flush(self) -> None:
        if not self.path or not self._dirty:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, separators=(",", ":")))
        tmp.replace(self.path)
        self._dirty = 0


class GmailSource:
    name = "gmail"
    item_sources = ("gmail",)

    def __init__(self, service_factory: Callable[[], object], months: int = 18, clock: Clock | None = None,
                 allow_domains=DEFAULT_ALLOW_DOMAINS, sleep: Callable[[float], None] = time.sleep,
                 cache_path: Path | None = None, pace: float | None = None):
        self._factory = service_factory
        self._sleep = sleep
        self.cache = VerdictCache(cache_path)
        self.pace = PACE_S if pace is None else pace
        self._gate = threading.Lock()
        self._last_load = 0.0
        self.load_gap = LOAD_GAP_S
        self.months = months
        self.clock = clock or Clock()
        self.allow_domains = tuple(allow_domains)
        self.stats: Counter = Counter()
        self._cursor: str | None = None

    def _svc(self):
        return self._factory()

    # ---- discovery
    def _list_thread_ids(self, svc, query: str) -> list[tuple[str, str]]:
        """(thread id, historyId). threads.list already returns the historyId, which is the cache key."""
        out: list[tuple[str, str]] = []
        token = None
        while True:
            r = _wrap(lambda: svc.users().threads().list(userId="me", q=query, maxResults=500,
                                                         pageToken=token).execute())
            out += [(t["id"], str(t.get("historyId", ""))) for t in r.get("threads", [])]
            token = r.get("nextPageToken")
            if not token:
                return out

    def _item(self, thread: dict) -> RawItem:
        msgs = thread.get("messages") or []
        subject, last_ms, sender = "", 0, ""
        participants: set[str] = set()
        labels: set[str] = set()
        for m in msgs:
            h = _headers(m)
            subject = subject or h.get("subject", "")
            last_ms = max(last_ms, int(m.get("internalDate", 0) or 0))
            labels.update(m.get("labelIds") or [])
            addr = parseaddr(h.get("from", ""))[1].lower()
            if addr:
                participants.add(addr)
                if not sender and addr != ALLOWED_ACCOUNT:
                    sender = addr
        kind = "mail_thread"
        modified = datetime.fromtimestamp(last_ms / 1000, timezone.utc).isoformat() if last_ms else None
        return RawItem(
            source="gmail", kind=kind, locator=f"gmail:thread:{thread['id']}", title=subject or "(no subject)",
            modified_at=modified,
            meta={"rev": str(thread.get("historyId", "")), "participants": sorted(participants),
                  "labels": sorted(labels), "sender": sender,
                  **({"sender_handle": ["email", sender.lower()]} if sender else {})})

    def discover(self, cursor: str | None, limit: int | None = None) -> Iterator[RawItem]:
        svc = self._svc()
        if cursor:
            ids = self._changed_since(svc, cursor)
        else:
            self._cursor = _wrap(lambda: svc.users().getProfile(userId="me").execute())["historyId"]
            after = (datetime.fromtimestamp(self.clock.now(), timezone.utc) - timedelta(days=30 * self.months))
            ids = self._list_thread_ids(svc, f"after:{after:%Y/%m/%d} -in:spam -in:trash")
        for tid, v, item in self._classified(svc, ids):
            if limit and self.stats["threads"] >= limit:
                self._cursor = None        # truncated run: the rest must still be found by the next full discover
                break
            if v is None:
                continue
            self.stats[("keep_" if v.keep else "skip_") + v.rule] += 1
            if not v.keep:
                continue                   # junk is never stored, embedded or sent anywhere
            self.stats["threads"] += 1
            yield item
        self.cache.flush()

    def _classified(self, svc, ids: list[tuple[str, str]]):
        """Yield (thread id, Verdict | None, RawItem | None) in order. None verdict = deleted since it was listed.
        A cached verdict for an unchanged thread costs no API call."""
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            hits = {tid: self.cache.get(tid, hid) for tid, hid in chunk}
            got = self._batch(svc, [tid for tid, _ in chunk if hits[tid] is None])
            for tid, hid in chunk:
                if hits[tid] is not None:
                    e = hits[tid]
                    item = None
                    if e["k"] and e.get("i"):
                        old = e["i"]["meta"]
                        fields = {**e["i"], "meta": {
                            **old, "keep_rule": e["r"],
                            **({"sender_handle": ["email", old["sender"]]}
                               if old.get("sender") and "sender_handle" not in old else {})}}
                        item = RawItem(source="gmail", kind="mail_thread", locator=f"gmail:thread:{tid}", **fields)
                    yield tid, Verdict(e["k"], e["r"]), item
                    continue
                thread = got.get(tid)
                if thread is None:
                    yield tid, None, None
                    continue
                v = classify(thread, ALLOWED_ACCOUNT, self.allow_domains)
                item = self._item(thread) if v.keep else None
                if item:
                    item.meta["keep_rule"] = v.rule        # why it was kept: the worth gate trusts the allowlist
                fields = {"title": item.title, "modified_at": item.modified_at, "meta": item.meta} if item else None
                self.cache.put(tid, hid or str(thread.get("historyId", "")), v.rule, v.keep, fields)
                yield tid, v, item

    def _batch(self, svc, ids: list[str]) -> dict[str, dict | None]:
        """threads.get (metadata) for ids, batched, paced to the quota. None = deleted since it was listed."""
        got: dict[str, dict | None] = {}
        if not ids:
            return got
        todo = list(ids)
        for _ in range(10):
            slow: list[str] = []

            def cb(request_id, response, exc):
                if exc is None:
                    got[request_id] = response
                elif not isinstance(exc, HttpError):
                    raise exc
                elif _http_status(exc) == 404:
                    got[request_id] = None
                elif _rate_limited(exc):
                    slow.append(request_id)
                elif _http_status(exc) in (401, 403):
                    raise Fatal("NEEDS_AUTH")
                else:
                    raise exc
            batch = svc.new_batch_http_request(callback=cb)
            for tid in todo:
                batch.add(svc.users().threads().get(userId="me", id=tid, format="metadata",
                                                    metadataHeaders=METADATA_HEADERS), request_id=tid)
            _wrap(batch.execute)
            if not slow:
                if self.pace:
                    self._sleep(self.pace)
                return got
            todo = slow
            self.cache.flush()
            self.pace = min(max(self.pace, 1.0) * 1.5, 30.0)       # we went too fast: slow down for good
            self._sleep(LIMIT_WAIT_S)
        raise Retryable("GMAIL_RATE_LIMIT")

    def _changed_since(self, svc, cursor: str) -> list[tuple[str, str]]:
        ids: dict[str, str] = {}
        token = None
        try:
            while True:
                r = _wrap(lambda: svc.users().history().list(
                    userId="me", startHistoryId=cursor, historyTypes=["messageAdded"],
                    pageToken=token).execute())
                for h in r.get("history", []):
                    for m in h.get("messagesAdded", []):
                        ids[m["message"]["threadId"]] = ""        # no historyId here: always re-read
                token = r.get("nextPageToken")
                if not token:
                    break
            self._cursor = str(r.get("historyId") or cursor)
        except HttpError as e:
            if _http_status(e) != 404:
                raise
            # history expired (Gmail keeps about a week): resync the last 30 days
            self._cursor = _wrap(lambda: svc.users().getProfile(userId="me").execute())["historyId"]
            after = datetime.fromtimestamp(self.clock.now(), timezone.utc) - timedelta(days=RESYNC_DAYS)
            return self._list_thread_ids(svc, f"after:{after:%Y/%m/%d} -in:spam -in:trash")
        return list(ids.items())

    def next_cursor(self) -> str | None:
        return self._cursor

    def _get_full(self, svc, tid: str) -> dict:
        """One full thread, serialized and spaced so parallel prep workers stay inside the per-minute quota.
        A limit waits out the window and slows down, rather than failing the job."""
        for attempt in range(6):
            with self._gate:
                wait = self._last_load + self.load_gap - time.monotonic()
                if wait > 0:
                    self._sleep(wait)
                try:
                    return _wrap(lambda: svc.users().threads().get(userId="me", id=tid, format="full").execute())
                except Retryable:
                    if attempt == 5:
                        raise
                    self.load_gap = min(max(self.load_gap, 0.25) * 1.5, 10.0)
                    self._sleep(LIMIT_WAIT_S)
                finally:
                    self._last_load = time.monotonic()
        raise Retryable("GMAIL_RATE_LIMIT")

    # ---- text
    def load_text(self, item: RawItem) -> str:
        tid = item.locator.rsplit(":", 1)[1]
        svc = self._svc()
        thread = self._get_full(svc, tid)
        parts = []
        for m in thread.get("messages") or []:
            h = _headers(m)
            ts = int(m.get("internalDate", 0) or 0) / 1000
            date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else "?"
            who = parseaddr(h.get("from", ""))[0] or parseaddr(h.get("from", ""))[1]
            body = message_text(m)
            if body:
                parts.append(f"[{date}] From: {who}\n{body}")
        subject = next((_headers(m).get("subject") for m in thread.get("messages") or [] if _headers(m).get("subject")), "")
        return (f"Subject: {subject}\n" if subject else "") + "\n\n".join(parts)


def measure(service_factory, sample: int = 30, months: int = 18, clock: Clock | None = None,
            rng: random.Random | None = None, allow_domains=DEFAULT_ALLOW_DOMAINS,
            cache_path: Path | None = None, sleep: Callable[[float], None] = time.sleep,
            pace: float | None = None) -> dict:
    """Page thread ids, run the free pre-filter on every thread's metadata, then fetch a random sample of the
    survivors to estimate tokens per thread. "threads" is the number that would reach Jev and Haiku.
    Verdicts are cached on disk, so a run that is interrupted (or a later build) does not start again."""
    src = GmailSource(service_factory, months, clock, allow_domains, sleep, cache_path, pace)
    svc = service_factory()
    after = datetime.fromtimestamp((clock or Clock()).now(), timezone.utc) - timedelta(days=30 * months)
    ids = src._list_thread_ids(svc, f"after:{after:%Y/%m/%d} -in:spam -in:trash")
    kept: list[str] = []
    rules: Counter = Counter()
    try:
        for tid, v, _ in src._classified(svc, ids):
            if v is None:
                continue
            rules[v.rule] += 1
            if v.keep:
                kept.append(tid)
    finally:
        src.cache.flush()
    pick = (rng or random.Random(7)).sample(kept, min(sample, len(kept)))
    tokens = []
    for tid in pick:
        item = RawItem(source="gmail", kind="mail_thread", locator=f"gmail:thread:{tid}", title="")
        tokens.append(approx_tokens(src.load_text(item)))
    avg = sum(tokens) / len(tokens) if tokens else 0.0
    return {"listed": len(ids), "threads": len(kept), "avg_tokens": round(avg, 1),
            "est_total_tokens": round(len(kept) * avg),
            "kept": {r: rules[r] for r in KEEP_RULES if rules[r]},
            "skipped": {r: rules[r] for r in SKIP_RULES if rules[r]}}
