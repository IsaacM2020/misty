"""Voice replies and the address word (the name he asked to be called, "boss"). Prompt rules alone did not hold, so
code does it, as a tiny streaming filter.

- `address_allowed`: the word may appear in at most 1 of every 4 assistant replies (not in any of the last 3).
- allowed: only a trailing `, <word>.` is fixed (a reply must never END on the name).
- not allowed: the word is removed as the reply streams: a leading "Boss, " / "Boss! " (next word capitalised), and
  ", boss" before . ! ? or a comma ("Not much, boss." -> "Not much.", "Okay, boss, sure" -> "Okay, sure").
The filter holds back only a few characters: a leading fragment of the word, or a comma that could still turn into
`,\\s*<word>` followed by its punctuation. Anything else is released at once, unchanged."""
from __future__ import annotations

import re

INTERJ = ("yeah", "yes", "yep", "okay", "ok", "sure", "alright", "right", "thanks", "cheers", "hey", "hi", "nice", "good", "got it")
_INTERJ_RX = "(?:" + "|".join(sorted((i.replace(" ", r"\s+") for i in INTERJ), key=len, reverse=True)) + ")"
RECENT = 3          # replies looked back at: the word was in none of them = it may be used (1 in 4)


def address_allowed(con, word: str, recent: int = RECENT) -> bool:
    w = (word or "").strip()
    if not w:
        return True
    rx = re.compile(r"\b" + re.escape(w) + r"\b", re.I)
    rows = con.execute("SELECT text FROM chat_messages WHERE role='assistant' ORDER BY id DESC LIMIT ?", (recent,)).fetchall()
    return not any(rx.search(r[0] or "") for r in rows)


class AddressTailGuard:
    def __init__(self, word: str = "", allowed: bool = True):
        self.word = (word or "").strip().lower()
        self.allowed = allowed
        self.held = ""
        self.out = ""                 # everything released so far (a reply that is only ", boss." is left alone)

    # ---- allowed: never END on the word
    def _could_match(self, tail: str) -> bool:
        s = tail[1:].lstrip()
        if not s:
            return True
        low = s.lower()
        if len(low) <= len(self.word) and self.word.startswith(low):
            return True
        return low.startswith(self.word) and re.fullmatch(r"\s*[.!]?\s*", low[len(self.word):]) is not None

    # ---- not allowed: take it out wherever it is a vocative
    def _lead_open(self, buf: str) -> bool:
        """buf could still become a leading `Boss,` / `Boss!` plus the first letter of what follows."""
        low = buf.lower().lstrip()
        if not low or (len(low) <= len(self.word) and self.word.startswith(low)):
            return True
        return low.startswith(self.word) and re.fullmatch(r"\s*[,!.]?\s*", low[len(self.word):]) is not None

    def _interj_open(self, buf: str) -> bool:
        """buf could still become a leading interjection + the word + , . ! or a space ("Yeah boss," / "Sure boss.")."""
        low = " ".join(buf.lower().split()) + (" " if buf[-1:].isspace() else "")
        for i in INTERJ:
            if i.startswith(low.lstrip()) and low.strip():
                return True
            if low.lstrip().startswith(i + " "):
                rest = low.lstrip()[len(i) + 1:]
                if self.word.startswith(rest) or rest == self.word:
                    return True
        return False

    def _comma_open(self, tail: str) -> bool:
        """tail (starting at a comma) could still become `, boss` + [.!?,]."""
        s = tail[1:].lstrip()
        low = s.lower()
        if not low or (len(low) <= len(self.word) and self.word.startswith(low)):
            return True
        return low.startswith(self.word) and re.fullmatch(r"[ \t]*", low[len(self.word):]) is not None

    def _strip(self, buf: str, at_start: bool) -> tuple[str, str]:
        """(text safe to send, text still held) for buf with every complete vocative removed."""
        w = re.escape(self.word)
        if at_start:
            m = re.match(r"\s*" + w + r"\s*[,!.]\s*(\S)", buf, re.I)
            mi = re.match(r"(\s*" + _INTERJ_RX + r")\s+" + w + r"(?=[,.!\s])", buf, re.I)
            if m:
                buf = m.group(1).upper() + buf[m.end():]
            elif mi:                                           # "Yeah boss, ..." -> "Yeah, ...": keep the interjection
                buf = mi.group(1) + buf[mi.end():]
            elif self._lead_open(buf) or self._interj_open(buf):
                return "", buf
        buf = re.sub(r",[ \t]*" + w + r"[ \t]*([.!?,])", r"\1", buf, flags=re.I)
        i = next((i for i, c in enumerate(buf) if c == "," and self._comma_open(buf[i:])), len(buf))
        return buf[:i], buf[i:]

    def feed(self, piece: str) -> str:
        """Text safe to send now (the rest, if any, stays held)."""
        if not self.word:
            return piece
        buf = self.held + piece
        if self.allowed:
            i = next((i for i, c in enumerate(buf) if c == "," and self._could_match(buf[i:])), len(buf))
            send, self.held = buf[:i], buf[i:]
        else:
            send, self.held = self._strip(buf, at_start=not self.out)
        self.out += send
        return send

    def flush(self) -> str:
        """End of the reply: the held tail, with a trailing ", <word>" turned into the full stop (or "!")."""
        held, self.held = self.held, ""
        if not held:
            return ""
        if self.word and not self.allowed and not self.out.strip():
            mi = re.fullmatch(r"(\s*" + _INTERJ_RX + r")\s+" + re.escape(self.word) + r"\s*", held, re.I)
            if mi:                                             # a reply that is only "Sure boss"
                self.out += mi.group(1)
                return mi.group(1)
        m = re.fullmatch(r",\s*" + re.escape(self.word) + r"\s*([.!]?)\s*", held, re.I) if self.word else None
        text = ("!" if m.group(1) == "!" else ".") if m and self.out.strip() else held
        self.out += text
        return text
