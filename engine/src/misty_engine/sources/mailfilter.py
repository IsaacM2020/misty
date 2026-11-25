"""Free, local mail pre-filter (decision P33).

Decides from labels, headers and sender only. No message body is read and nothing is sent anywhere.
Junk that is skipped here is never stored, never embedded and never reaches Jev or Haiku.

Order matters, first match wins:
  keep   replied     I sent a message to someone else (SENT label or my address, not mail to myself)
  keep   starred     any message is starred
  keep   allowlist   a sender is on an allowed domain (Google Classroom, school, ...)
  skip   promotions  Gmail Promotions category
  skip   social      Gmail Social category
  skip   bulk_unsubscribe  a message carries a List-Unsubscribe header
  skip   bulk_precedence   Precedence: bulk / junk / list
  skip   auto_submitted    Auto-Submitted other than "no"
  skip   automated_sender  every sender is a no-reply / notification style address
  keep   human       everything else (Primary, a real person writing)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import getaddresses, parseaddr
from typing import Iterable

DEFAULT_ALLOW_DOMAINS = ("classroom.google.com",)

_AUTOMATED_ANYWHERE = re.compile(r"no[-_.]?reply|do[-_.]?not[-_.]?reply")
_AUTOMATED_START = re.compile(
    r"^(?:notifications?|notify|mailer-daemon|postmaster|bounces?|automated|alerts?|newsletters?)(?:[-_.+=].*)?$")
_BULK_PRECEDENCE = {"bulk", "junk", "list"}

SKIP_RULES = ("promotions", "social", "bulk_unsubscribe", "bulk_precedence", "auto_submitted", "automated_sender")
KEEP_RULES = ("replied", "starred", "allowlist", "human")


@dataclass(frozen=True)
class Verdict:
    keep: bool
    rule: str


def _msg_headers(msg: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in (msg.get("payload", {}).get("headers") or [])}


def is_automated_address(addr: str) -> bool:
    local = addr.lower().split("@", 1)[0]
    return bool(_AUTOMATED_ANYWHERE.search(local) or _AUTOMATED_START.match(local))


def domain_allowed(addr: str, allow: Iterable[str]) -> bool:
    dom = addr.lower().rsplit("@", 1)[-1] if "@" in addr else ""
    return any(dom == a or dom.endswith("." + a) for a in (x.lower().lstrip("@.") for x in allow) if a)


def classify(thread: dict, me: str, allow_domains: Iterable[str] = DEFAULT_ALLOW_DOMAINS) -> Verdict:
    """thread is a Gmail threads.get (format=metadata) payload."""
    me = me.lower()
    msgs = thread.get("messages") or []
    labels: set[str] = set()
    senders: list[str] = []
    my_recipients: set[str] = set()
    bulk = prec = auto = False
    for m in msgs:
        labels.update(m.get("labelIds") or [])
        h = _msg_headers(m)
        addr = parseaddr(h.get("from", ""))[1].lower()
        if addr:
            senders.append(addr)
        if addr == me or "SENT" in (m.get("labelIds") or []):
            my_recipients.update(a.lower() for _, a in getaddresses([h.get("to", ""), h.get("cc", "")]) if a)
        bulk = bulk or "list-unsubscribe" in h
        prec = prec or h.get("precedence", "").strip().lower() in _BULK_PRECEDENCE
        a = h.get("auto-submitted", "").strip().lower()
        auto = auto or (a not in ("", "no"))
    others = [s for s in senders if s != me]

    # mail I sent only to myself (server reports, notes to self) is not a conversation
    sent = "SENT" in labels or me in senders
    if sent and (others or (my_recipients - {me})):
        return Verdict(True, "replied")
    if "STARRED" in labels:
        return Verdict(True, "starred")
    allow = tuple(allow_domains)
    if any(domain_allowed(s, allow) for s in others):
        return Verdict(True, "allowlist")
    if "CATEGORY_PROMOTIONS" in labels:
        return Verdict(False, "promotions")
    if "CATEGORY_SOCIAL" in labels:
        return Verdict(False, "social")
    if bulk:
        return Verdict(False, "bulk_unsubscribe")
    if prec:
        return Verdict(False, "bulk_precedence")
    if auto:
        return Verdict(False, "auto_submitted")
    if others and all(is_automated_address(s) for s in others):
        return Verdict(False, "automated_sender")
    return Verdict(True, "human")
