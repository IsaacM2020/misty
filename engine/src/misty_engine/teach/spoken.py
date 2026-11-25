"""E8: his voice. A spoken answer arrives as a transcript, so a number comes as words and a question has to suit the ear.

Pure code. `words_to_number` reads "nine point eight metres per second squared" as (9.8, "metres per second squared").
`for_ear` cuts a long question into two short parts with a pause between them.
"""
from __future__ import annotations

import re

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen".split())}
_UNITS["nought"] = _UNITS["oh"] = 0
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"thousand": 1_000, "million": 1_000_000}
_NEG = ("minus", "negative")
_WORD = re.compile(r"[a-z]+|\d+")
_TEXT_MAX = 200
SPEECH_WORDS = 30          # a question read aloud stays in parts of at most this many words (a sentence is not split below it)


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("-", " ").replace("'", ""))


def _is_num(t: str) -> bool:
    return t in _UNITS or t in _TENS or t in ("hundred", "a") or t in _SCALES


def _integer(toks: list[str], i: int) -> tuple[int | None, int]:
    """Read a whole number ("three hundred and forty two") starting at i. Returns (value, next index) or (None, i)."""
    total, cur, j, seen = 0, 0, i, False
    while j < len(toks):
        t = toks[j]
        if t in _UNITS and not (t == "oh" and not seen):
            if seen and cur % 10 != 0 and cur and cur < 100 and cur not in _TENS.values():
                break                                           # "five five": two numbers, stop at the first
            cur += _UNITS[t]
        elif t in _TENS:
            if seen and cur % 100 >= 10:
                break
            cur += _TENS[t]
        elif t == "a" and j + 1 < len(toks) and toks[j + 1] in ("hundred", "thousand", "million") and not seen:
            cur += 1
        elif t == "hundred" and seen or (t == "hundred" and j > i and toks[j - 1] == "a"):
            cur = (cur or 1) * 100
        elif t in _SCALES and (seen or (j > i and toks[j - 1] == "a")):
            total += (cur or 1) * _SCALES[t]
            cur = 0
        elif t == "and" and seen and j + 1 < len(toks) and (toks[j + 1] in _UNITS or toks[j + 1] in _TENS):
            j += 1
            continue
        else:
            break
        seen = True
        j += 1
    return (total + cur, j) if seen else (None, i)


def words_to_number(text: str) -> tuple[float, str] | None:
    """The first spoken number in `text` and the words left after it (his unit). None when there is none."""
    t = (text or "").strip()
    if not t or len(t) > _TEXT_MAX:
        return None
    toks = _tokens(t)
    n = len(toks)
    i = 0
    while i < n:
        j, neg = i, False
        if toks[j] in _NEG and j + 1 < n:
            neg, j = True, j + 1
        whole, k = _integer(toks, j)
        if whole is None and not (toks[j] == "point" and j + 1 < n and toks[j + 1] in _UNITS):
            i += 1
            continue
        val = float(whole or 0)
        if toks[k:k + 1] == ["point"]:
            digs, m = [], k + 1
            while m < n and toks[m] in _UNITS and _UNITS[toks[m]] <= 9:
                digs.append(str(_UNITS[toks[m]]))
                m += 1
            if digs:
                val = float(f"{int(val)}." + "".join(digs))
                k = m
        elif toks[k:k + 1] == ["over"]:
            den, m = _integer(toks, k + 1)
            if den:
                val, k = val / den, m
        elif toks[k:k + 1] == ["squared"]:
            val, k = val * val, k + 1
        if toks[k:k + 2] == ["times", "ten"] and k + 2 < n:         # "three times ten to the minus four"
            m = k + 2
            while m < n and toks[m] in ("to", "the", "power", "of"):
                m += 1
            eneg = m < n and toks[m] in _NEG
            m += 1 if eneg else 0
            exp, m2 = _integer(toks, m)
            if exp is not None:
                val *= 10.0 ** (-exp if eneg else exp)
                k = m2
        return (-val if neg else val), " ".join(toks[k:])
    return None


def unit_from_words(unit: str) -> str:
    """A spoken unit as the written form `keys.parse_unit` reads: "metres per second squared" -> "metres/second^2"."""
    u = " ".join((unit or "").lower().split())
    if " per " not in f" {u} " and "squared" not in u and "cubed" not in u:
        return unit
    u = re.sub(r"\s*\bper\b\s*", "/", f" {u} ".strip())
    u = re.sub(r"\s*\bsquared\b", "^2", u)
    u = re.sub(r"\s*\bcubed\b", "^3", u)
    return u.strip()


# ---- voice-sized text --------------------------------------------------------------------------------------------
def for_ear(q: str) -> str:
    """A question of more than SPEECH_WORDS words is two parts split by a blank line (a pause when spoken). No word
    is changed or dropped. The cut goes at the sentence end nearest the middle, else at a comma, else it is left alone."""
    q = (q or "").strip()
    words = q.split()
    if len(words) <= SPEECH_WORDS:
        return q
    cuts = [m.end() for m in re.finditer(r"[.!?;]\s+", q)] or [m.end() for m in re.finditer(r",\s+", q)]
    best = None
    for c in cuts:
        a, b = len(q[:c].split()), len(q[c:].split())
        if a == 0 or b == 0 or a > SPEECH_WORDS and len(cuts) > 1 and False:
            continue
        if best is None or abs(a - b) < abs(best[1] - best[2]):
            best = (c, a, b)
    if best is None:
        return q
    c = best[0]
    return q[:c].strip() + "\n\n" + q[c:].strip()
