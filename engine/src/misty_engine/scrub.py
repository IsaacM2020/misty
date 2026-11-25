"""Local secret and PII scrubber. Pure code, no LLM. Runs before anything leaves the Mac."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Callable, Literal

Context = Literal["file", "mail", "message"]

SECRET_NAME_RE = re.compile(r"(?i)secret|password|passwd|credential|token")
SECRET_GLOBS = (".env", ".env*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "id_ecdsa*", "*.p12",
                "*.pfx", "*.keychain", "*.keychain-db", "*.kdbx", "*.1pux", "*.1pif")


def is_secret_file(path) -> bool:
    """These files are indexed by filename only and never opened."""
    name = PurePath(str(path)).name
    return any(PurePath(name).match(g) for g in SECRET_GLOBS) or bool(SECRET_NAME_RE.search(name))


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _looks_secret(tok: str) -> bool:
    longest = max(re.split(r"[-_/.]", tok), key=len)
    if len(longest) < 24:
        return False
    if not (re.search(r"[A-Za-z]", longest) and re.search(r"\d", longest)):
        return False
    return _entropy(longest) >= 3.5


_TIMESTAMP14 = re.compile(r"^(?:19|20)\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:[01]\d|2[0-3])[0-5]\d[0-5]\d$")


def _card_ok(m: re.Match) -> bool:
    d = re.sub(r"\D", "", m.group(0))
    if _TIMESTAMP14.match(d):                    # file names like name-20260605-121306 pass Luhn 1 time in 10
        return False
    return 13 <= len(d) <= 19 and luhn_ok(d)


# (kind, pattern, group to redact (0 = whole match), extra check or None, contexts or None = all)
RULES: list[tuple[str, re.Pattern, int, Callable[[re.Match], bool] | None, tuple[str, ...] | None]] = [
    ("private_key", re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?(?:-----END[A-Z ]*PRIVATE KEY-----|\Z)", re.S), 0, None, None),
    ("api_key", re.compile(
        r"\b(?:sk-or-v1-[A-Za-z0-9]{16,}|sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{30,}"
        r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|xox[abposr]-[A-Za-z0-9\-]{10,}"
        r"|AKIA[0-9A-Z]{16}|gsk_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{30,})"), 0, None, None),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), 0, None, None),
    ("password", re.compile(
        r"(?i)\b(?:password|passwd|passcode|pwd|secret|api[_\- ]?key|access[_\- ]?token|auth[_\- ]?token)\b"
        r"\s*[:=]\s*(\"[^\"\n]*\"|'[^'\n]*'|[^\s\[]\S*)"), 1, None, None),
    ("card", re.compile(r"\b(?:\d[ \-]?){12,18}\d\b"), 0, _card_ok, None),
    ("nric", re.compile(r"(?i)\b[STFGM]\d{7}[A-Z]\b"), 0, None, None),
    ("otp", re.compile(
        r"(?i)\b(?:otp|one[- ]time (?:password|pin|code)|verification code|security code|passcode|pin|code)\b"
        r"[^0-9\n]{0,25}\b(\d{4,8})\b"), 1, None, ("mail", "message")),
    ("bank_account", re.compile(
        r"(?i)\b(?:a/c|acct|account)(?:\s*(?:no\.?|number|#))?\s*[:\-]?\s*(\d[\d\- ]{6,18}\d)"), 1, None, None),
    ("bank_account", re.compile(r"\b\d{3}-\d{5,6}-\d{1,3}\b"), 0, None, None),
    ("high_entropy", re.compile(r"[A-Za-z0-9+/=_\-.]{32,}"), 0, lambda m: _looks_secret(m.group(0)), None),
]


@dataclass
class ScrubResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


def scrub_text(text: str, context: Context = "file") -> ScrubResult:
    counts: Counter = Counter()
    for kind, pat, grp, check, ctxs in RULES:
        if ctxs and context not in ctxs:
            continue

        def repl(m: re.Match, kind=kind, grp=grp, check=check) -> str:
            if check is not None and not check(m):
                return m.group(0)
            counts[kind] += 1
            if grp == 0:
                return f"[REDACTED:{kind}]"
            s, e = m.span(grp)
            base = m.start(0)
            whole = m.group(0)
            return whole[: s - base] + f"[REDACTED:{kind}]" + whole[e - base:]

        text = pat.sub(repl, text)
    return ScrubResult(text, dict(counts))


def find_secrets(text: str, context: Context = "file") -> list[str]:
    return sorted(scrub_text(text, context).counts)
