"""The safety gate: plain code every action passes before it runs. The brain cannot talk its way past it.

ask   = stop and ask Isaac; run it only on his yes.
never = Stream does not do it at all (typing a password or card number). Isaac does it himself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..scrub import find_secrets, luhn_ok


def _words(*ws: str) -> re.Pattern:
    return re.compile(r"(?i)\b(?:" + "|".join(ws) + r")\b")


LABEL_RISK: list[tuple[re.Pattern, str]] = [
    (_words("place order", "buy", "purchase", "pay", "checkout"), "buy something"),
    (_words("send", "reply all"), "send a message"),
    (_words("post", "publish", "tweet"), "post something"),
    (_words("delete", "remove", "trash", "empty trash", "erase"), "delete something"),
    (_words("submit"), "submit a form"),
]
# Keys that send or delete without a button (Mail's cmd+return, Finder's move-to-trash, empty trash).
RISKY_KEYS = {"cmd+return": "send a message", "cmd+enter": "send a message", "cmd+shift+d": "send a message",
              "cmd+delete": "delete something", "cmd+backspace": "delete something",
              "cmd+shift+delete": "empty the trash", "cmd+alt+shift+delete": "empty the trash",
              "cmd+q": "quit an app", "cmd+alt+escape": "force quit an app"}
_MODS = {"command": "cmd", "meta": "cmd", "option": "alt", "opt": "alt", "control": "ctrl", "enter": "return"}
_CARD = re.compile(r"(?:\d[ \-]?){12,18}\d")


@dataclass(frozen=True)
class Verdict:
    kind: str          # ok | ask | never
    why: str = ""


OK = Verdict("ok")


def norm_keys(combo: str) -> str:
    parts = [_MODS.get(p, p) for p in re.split(r"[+\-\s]+", combo.strip().lower()) if p]
    mods = sorted(p for p in parts[:-1])
    return "+".join(mods + parts[-1:])


def looks_secret(text: str) -> bool:
    if find_secrets(text, "file"):
        return True
    for m in _CARD.finditer(text):
        d = re.sub(r"\D", "", m.group(0))
        if 13 <= len(d) <= 19 and luhn_ok(d):
            return True
    return False


def label_risk(label: str) -> str | None:
    for pat, what in LABEL_RISK:
        if pat.search(label or ""):
            return what
    return None


def check(action: str, *, label: str = "", text: str = "", keys: str = "", secure_field: bool = False,
          url: str = "") -> Verdict:
    if action in ("type_into", "type_keys") and (secure_field or looks_secret(text)):
        return Verdict("never", "type a password or card number")
    if action in ("press", "menu"):
        what = label_risk(label)
        if what:
            return Verdict("ask", what)
    if action == "shortcut":
        k = norm_keys(keys)
        for combo, what in RISKY_KEYS.items():
            if k == norm_keys(combo):
                return Verdict("ask", what)
    if action == "open_url" and re.search(r"(?i)/(?:checkout|payment|pay|billing)(?:[/?#]|$)", url):
        return Verdict("ask", "open a payment page")
    return OK
