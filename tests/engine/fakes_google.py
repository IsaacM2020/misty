"""Fake Gmail / Calendar service objects with canned data (no network)."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import httplib2
from googleapiclient.errors import HttpError


def http_error(status: int, reason: str = "") -> HttpError:
    body = {"error": {"errors": [{"reason": reason}]}} if reason else {}
    return HttpError(httplib2.Response({"status": status}), json.dumps(body).encode())


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def make_thread(tid: str, subject: str = "Hello", body: str = "Hi Isaac, deadline moved to Friday.",
                sender: str = "Aarav Shah <aarav@example.com>", labels=("INBOX",), when=(2026, 10, 12),
                history_id: str = "100", html: bool = False, headers: dict | None = None,
                reply_from: tuple[str, tuple] | None = None) -> dict:
    """headers: extra headers on the first message. reply_from: (sender, labels) adds a second message."""
    ms = int(datetime(*when, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    mime = "text/html" if html else "text/plain"

    def msg(n: int, frm: str, lbls, extra: dict) -> dict:
        hdrs = [{"name": "Subject", "value": subject}, {"name": "From", "value": frm}]
        hdrs += [{"name": k, "value": v} for k, v in extra.items()]
        return {"id": f"{tid}m{n}", "threadId": tid, "labelIds": list(lbls), "internalDate": str(ms + n),
                "payload": {"mimeType": "multipart/alternative", "headers": hdrs,
                            "parts": [{"mimeType": mime, "body": {"data": b64(body)}}]}}
    msgs = [msg(1, sender, labels, headers or {})]
    if reply_from:
        msgs.append(msg(2, reply_from[0], reply_from[1], {}))
    return {"id": tid, "historyId": history_id, "messages": msgs}


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeGmail:
    def __init__(self, threads: list[dict] | None = None, history_id: str = "500"):
        self.data = {t["id"]: t for t in (threads or [make_thread("t1"), make_thread("t2"), make_thread("t3")])}
        self.history_id = history_id
        self.calls_used: set[str] = set()
        self.history_events: list[tuple[str, str]] = []     # (historyId, threadId)
        self.expire_history = False
        self.list_queries: list[str] = []
        self.rate_limits = 0                                # next N threads.get calls fail with a 403 rateLimit

    def factory(self):
        return self

    def add_thread(self, tid: str, **kw) -> None:
        self.data[tid] = make_thread(tid, **kw)
        self.history_id = str(int(self.history_id) + 1)
        self.history_events.append((self.history_id, tid))

    # service chain
    def users(self):
        return self

    def threads(self):
        return _Threads(self)

    def new_batch_http_request(self, callback=None):
        return _Batch(callback)

    def history(self):
        return _History(self)

    def getProfile(self, userId):
        self.calls_used.add("getProfile")
        return _Req(lambda: {"emailAddress": "you@example.com", "historyId": self.history_id})


class _Batch:
    def __init__(self, callback):
        self.cb, self.items = callback, []

    def add(self, req, request_id=None, callback=None):
        self.items.append((request_id, req))

    def execute(self):
        for rid, req in self.items:
            try:
                self.cb(rid, req.execute(), None)
            except HttpError as e:
                self.cb(rid, None, e)


class _Threads:
    def __init__(self, g: FakeGmail):
        self.g = g

    def list(self, userId, q="", maxResults=100, pageToken=None):
        self.g.calls_used.add("threads.list")
        self.g.list_queries.append(q)
        return _Req(lambda: {"threads": [{"id": t, "historyId": self.g.data[t]["historyId"]}
                                         for t in self.g.data]})

    def get(self, userId, id, format="full", metadataHeaders=None):
        self.g.calls_used.add("threads.get")

        def run():
            if self.g.rate_limits > 0:
                self.g.rate_limits -= 1
                raise http_error(403, "rateLimitExceeded")
            if id not in self.g.data:
                raise http_error(404)
            return self.g.data[id]
        return _Req(run)


class _History:
    def __init__(self, g: FakeGmail):
        self.g = g

    def list(self, userId, startHistoryId, historyTypes=None, pageToken=None):
        self.g.calls_used.add("history.list")

        def run():
            if self.g.expire_history:
                raise http_error(404)
            evs = [e for e in self.g.history_events if int(e[0]) > int(startHistoryId)]
            return {"history": [{"messagesAdded": [{"message": {"threadId": t}}]} for _, t in evs],
                    "historyId": self.g.history_id}
        return _Req(run)


class FakeCal:
    def __init__(self, events: list[dict]):
        self.events_data = events
        self.version = 1
        self.changed: list[dict] = []
        self.expire = False
        self.calls: list[dict] = []

    def factory(self):
        return self

    def events(self):
        return self

    def list(self, calendarId, singleEvents=True, orderBy=None, timeMin=None, timeMax=None, syncToken=None,
             pageToken=None, maxResults=250):
        self.calls.append({"timeMin": timeMin, "timeMax": timeMax, "syncToken": syncToken})

        def run():
            if syncToken:
                if self.expire or syncToken == "expired":
                    raise http_error(410)
                return {"items": list(self.changed), "nextSyncToken": f"sync{self.version}"}
            items = self.events_data
            if timeMin and timeMax:
                lo, hi = datetime.fromisoformat(timeMin), datetime.fromisoformat(timeMax)
                items = [e for e in items if lo <= _dt(e) < hi]
            return {"items": items, "nextSyncToken": f"sync{self.version}"}
        return _Req(run)


def _dt(e: dict) -> datetime:
    s = e["start"]
    if "dateTime" in s:
        return datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00"))
    return datetime.fromisoformat(s["date"]).replace(tzinfo=timezone.utc)


def make_event(eid: str, summary: str, start: str, end: str, **kw) -> dict:
    d = {"id": eid, "summary": summary, "start": {"dateTime": start}, "end": {"dateTime": end}, "status": "confirmed"}
    d.update(kw)
    return d


class FakeCalWrite:
    """D4: a calendar she can add to. calendarList, events list/insert/get/delete, in memory. Records every write."""
    def __init__(self, calendars=None):
        self.cals = calendars or [{"id": "primary", "summary": "Isaac", "accessRole": "owner"},
                                  {"id": "school1", "summary": "School", "accessRole": "owner"},
                                  {"id": "hol", "summary": "Holidays in Singapore", "accessRole": "reader"}]
        self.events_by_cal: dict[str, dict[str, dict]] = {c["id"]: {} for c in self.cals}
        self.inserts: list[dict] = []
        self.deletes: list[str] = []
        self.lie = False                   # get() says the event isn't there (verify must fail)
        self.drop_after_insert = False     # the network drops after the server saved it
        self._n = 0

    def calendarList(self):
        return _CalList(self)

    def events(self):
        return _CalEvents(self)

    def add(self, cal: str, ev: dict) -> dict:
        self._n += 1
        ev = {"id": f"e{self._n}", "status": "confirmed", **ev}
        self.events_by_cal[cal][ev["id"]] = ev
        return ev


class _CalList:
    def __init__(self, c):
        self.c = c

    def list(self):
        return _Req(lambda: {"items": self.c.cals})


class _CalEvents:
    def __init__(self, c):
        self.c = c

    def list(self, calendarId, singleEvents=True, timeMin=None, timeMax=None, maxResults=250, **kw):
        def run():
            lo, hi = datetime.fromisoformat(timeMin), datetime.fromisoformat(timeMax)
            return {"items": [e for e in self.c.events_by_cal[calendarId].values() if lo <= _dt(e) < hi]}
        return _Req(run)

    def insert(self, calendarId, body):
        def run():
            ev = self.c.add(calendarId, json.loads(json.dumps(body)))
            self.c.inserts.append(ev)
            if self.c.drop_after_insert:
                raise ConnectionError("network dropped")
            return ev
        return _Req(run)

    def get(self, calendarId, eventId):
        def run():
            ev = self.c.events_by_cal.get(calendarId, {}).get(eventId)
            if ev is None or self.c.lie:
                raise http_error(404)
            return ev
        return _Req(run)

    def delete(self, calendarId, eventId):
        def run():
            self.c.events_by_cal[calendarId][eventId]["status"] = "cancelled"
            self.c.deletes.append(eventId)
            return ""
        return _Req(run)
