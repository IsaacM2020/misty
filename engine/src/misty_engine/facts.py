"""Facts: the things Isaac tells Misty to remember ("my English task is this doc"). One active fact per key.

A flat list on top of the wiki: the chat lists every active fact to the model, so recall of a fact is the model
reading a short list, not a search. Matching here is for Stream's context pack and for catching a near-duplicate key.
"""
from __future__ import annotations

import asyncio
import re
import secrets

import numpy as np

from .errors import Fatal
from .logs import log_event
from .scrub import find_secrets

KEY_MAX = 40
TEXT_MAX = 500
SAME_KEY_SIM = 0.86        # a new key this close to an existing one is the same thing said differently
LIST_MAX = 80              # facts shown to the chat model each turn


def norm_key(key: str) -> str:
    k = re.sub(r"[^a-z0-9 '&/-]+", " ", key.lower())
    return " ".join(k.split())[:KEY_MAX]


def _stem(key: str) -> str:
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in key.split())


def _embed(ctx, text: str) -> np.ndarray | None:
    if ctx.embedder is None:
        return None
    return np.asarray(ctx.embedder.embed_query(text), dtype=np.float32)


def active(con) -> list:
    return con.execute("SELECT * FROM facts WHERE status='active' ORDER BY updated_at DESC").fetchall()


def get(con, key: str):
    return con.execute("SELECT * FROM facts WHERE status='active' AND key=?", (norm_key(key),)).fetchone()


def _sim(a: np.ndarray | None, blob) -> float:
    if a is None or blob is None:
        return 0.0
    b = np.frombuffer(blob, dtype=np.float32)
    return float(np.dot(a, b)) if b.shape == a.shape else 0.0


def same_key(ctx, key: str, exact: bool = False) -> str | None:
    """An active key that means the same as `key`: exact first, then a close embedding of the key alone (not with `exact`)."""
    k = norm_key(key)
    if get(ctx.con, k) is not None:
        return k
    for r in active(ctx.con):                        # "physics tutors" is "physics tutor"
        if _stem(r["key"]) == _stem(k):
            return r["key"]
    if exact:
        return None
    q = _embed(ctx, k)
    best, best_sim = None, SAME_KEY_SIM
    for r in active(ctx.con):
        s = _sim(q, _embed(ctx, r["key"]).tobytes()) if q is not None else 0.0
        if s >= best_sim:
            best, best_sim = r["key"], s
    return best


def remember(ctx, key: str, text: str, link: str | None = None, link_title: str | None = None, exact: bool = False) -> dict:
    """Save or replace a fact. Returns {action: added|updated, key, id, was}. `exact`: only the same key is replaced, never one
    that merely means nearly the same (two classes' link keys differ by one word)."""
    k = norm_key(key)
    text = " ".join(str(text).split())[:TEXT_MAX]
    if not k or not text:
        raise Fatal("BAD_FACT")
    if find_secrets(" ".join(x for x in (key, text, link or "", link_title or "") if x), "file"):
        log_event("FACT_REFUSED_SECRET", n=1)
        raise Fatal("LOOKS_SECRET")
    k = same_key(ctx, k, exact) or k
    now = ctx.clock.now()
    fid = "f_" + secrets.token_hex(6)
    v = _embed(ctx, f"{k}: {text}" + (f" ({link_title})" if link_title else ""))
    old = get(ctx.con, k)
    with ctx.con:
        if old is not None:
            ctx.con.execute("UPDATE facts SET status='replaced', replaced_by=?, updated_at=? WHERE id=?",
                            (fid, now, old["id"]))
        ctx.con.execute(
            "INSERT INTO facts(id, key, text, link, link_title, vec, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?, 'active', ?, ?)",
            (fid, k, text, link, link_title, v.tobytes() if v is not None else None, now, now))
    log_event("FACT_SAVED", n=1)
    return {"action": "updated" if old is not None else "added", "key": k, "id": fid,
            "was": old["text"] if old is not None else None}


def forget(ctx, key_or_id: str) -> int:
    con, now = ctx.con, ctx.clock.now()
    with con:
        n = con.execute("UPDATE facts SET status='forgotten', updated_at=? WHERE status='active' AND (id=? OR key=?)",
                        (now, key_or_id, norm_key(key_or_id))).rowcount
    if n:
        log_event("FACT_FORGOTTEN", n=n)
    return n


def match(ctx, query: str, k: int = 3, min_score: float = 0.35) -> list[tuple[object, float]]:
    """Facts most related to `query`, best first: embedding similarity plus a bonus for key words in the query."""
    q = _embed(ctx, query)
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    out = []
    for r in active(ctx.con):
        kw = set(r["key"].split())
        bonus = 0.2 * len(kw & words) / max(1, len(kw))
        s = _sim(q, r["vec"]) + bonus
        if s >= min_score:
            out.append((r, s))
    out.sort(key=lambda t: -t[1])
    return out[:k]


OPEN_WORDS = re.compile(r"\b(do|open|start|work on|pull up|bring up|get|show|go to|continue|let'?s|time for|pls|please|"
                        r"wanna|want to|need to|finish)\b")
NOT_OPEN = re.compile(r"^\s*(what|where|which|when|who|why|how|is|does|did)\b|\b(is this|is that|is the|is my|remember|"
                      r"forget|change|update|rename|delete)\b")


def wants_open(ctx, text: str):
    """The one linked fact a message asks to open ("do my English task", "pull up the english test thing"), or None.
    Opening is decided here, not by the model: Haiku sometimes says "Opened it" without calling the tool."""
    t = text.lower()
    if not OPEN_WORDS.search(t) or NOT_OPEN.search(t):
        return None
    words = set(_stem(" ".join(re.findall(r"[a-z0-9]+", t))).split())
    hits = []
    for r in active(ctx.con):
        kw = set(_stem(r["key"]).split()) - {"my", "the", "a"}
        if r["link"] and kw and len(kw & words) / len(kw) >= 0.66:
            hits.append((len(kw & words) / len(kw), len(kw), r))
    hits.sort(key=lambda h: (-h[0], -h[1]))
    if not hits or (len(hits) > 1 and hits[1][:2] == hits[0][:2]):
        return None                                  # nothing, or two equally good: the model asks which
    return hits[0][2]


THIS = re.compile(r"\b(the|this|that) (page|doc|document|file|tab|link|pdf)( (he|isaac) (has|had) open)?( right now)?"
                  r"|\b(he|isaac) (has|had) open( right now)?|\bright now\b", re.I)


def name_this(text: str, title: str) -> str:
    """'my English task is the page he has open right now' -> '... is "Macbeth essay"': the link is saved anyway,
    and next month "right now" means nothing."""
    out = THIS.sub(f"\u201C{title}\u201D", text, count=1)
    return " ".join(THIS.sub("", out).split()).replace(" .", ".")


def line(r) -> str:
    s = f"- {r['key']}: {r['text']}"
    if r["link"]:
        s += f" [link: {r['link_title'] or r['link']}]"
    return s


def as_dict(r) -> dict:
    return {"id": r["id"], "key": r["key"], "text": r["text"], "link": r["link"], "link_title": r["link_title"],
            "updated_at": r["updated_at"]}


# ---- "this": what Isaac has in front of him (the app he was in before Misty)

BROWSER_TAB = {
    "com.apple.Safari": 'tell application id "com.apple.Safari" to get {URL, name} of current tab of front window',
    "com.google.Chrome": 'tell application id "com.google.Chrome" to get {URL, title} of active tab of front window',
    "com.brave.Browser": 'tell application id "com.brave.Browser" to get {URL, title} of active tab of front window',
}
FINDER = """tell application "Finder"
set s to selection as list
if (count of s) = 0 then return ""
set f to item 1 of s
return {POSIX path of (f as alias), name of f}
end tell"""
DOC_OF_PID = ('on run argv\ntell application "System Events" to tell (first process whose unix id is (item 1 of argv as integer))\n'
              'return {value of attribute "AXDocument" of front window, name of front window}\nend tell\nend run')


async def _osa(script: str, *args: str) -> list[str] | None:
    p = await asyncio.create_subprocess_exec("osascript", "-ss", "-e", script, *args,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), 15)
    except asyncio.TimeoutError:
        p.kill()                                     # almost always macOS asking "allow Misty to control X?"
        raise Fatal("AUTOMATION_PROMPT")
    if p.returncode != 0:
        return None
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', out.decode())
    return [x.replace('\\"', '"') for x in parts] if len(parts) >= 2 else None


async def front_thing(ctx) -> dict | None:
    """The page or file Isaac has open in the app he was using before Misty: {link, title, app}, or None."""
    hands = (ctx.extras or {}).get("hands")
    app = None
    if hands is not None and getattr(hands, "connected", False):
        try:
            app = await hands._call("last_app")
        except Exception:
            app = None
    bundle, pid, name = (app or {}).get("bundle_id", ""), (app or {}).get("pid"), (app or {}).get("name", "")
    got = None
    if bundle in BROWSER_TAB:
        got = await _osa(BROWSER_TAB[bundle])
    elif bundle == "com.apple.finder":
        got = await _osa(FINDER)
    elif pid:
        got = await _osa(DOC_OF_PID, str(pid))
        if got and got[0].startswith("file://"):
            from urllib.parse import unquote, urlparse
            got[0] = unquote(urlparse(got[0]).path)
    if not bundle:                                   # no app known: Safari's front tab is the best guess
        got = await _osa(BROWSER_TAB["com.apple.Safari"])
        name = "Safari"
    if not got or not got[0]:
        return None
    return {"link": got[0], "title": got[1] or got[0], "app": name}
