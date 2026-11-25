"""Web lookups for chat: a search (DuckDuckGo's plain HTML page, free, no key) and a page read. Questions like "what's
the Rubik's cube world record" get answered from here in a second or two, instead of Stream driving Safari to a page
it then can't read. What comes back is web text: data for the model, never instructions.
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from .scrub import scrub_text

SEARCH_URL = "https://html.duckduckgo.com/html/?q="
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Safari/605.1.15"
TIMEOUT = 6.0
PAGE_CHARS = 3000            # what a read hands the model: enough for an answer, cheap in tokens

_RESULT = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                     re.S)


def _text(s: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def _real_url(href: str) -> str:
    """DuckDuckGo wraps each link as //duckduckgo.com/l/?uddg=<url>."""
    q = parse_qs(urlparse(href).query).get("uddg")
    return unquote(q[0]) if q else href


def parse_results(page: str, k: int = 5) -> list[dict]:
    out = []
    for href, title, snippet in _RESULT.findall(page):
        url = _real_url(html.unescape(href))
        if "duckduckgo.com/y.js" in url:            # ads
            continue
        out.append({"title": _text(title)[:120], "url": url, "snippet": _text(snippet)[:300]})
        if len(out) == k:
            break
    return out


def page_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer)[^>]*>.*?</\1>", " ", page)
    return _text(page)


def around(text: str, find: str, chars: int = PAGE_CHARS) -> str:
    """The part of a long page that holds the words asked about, else its start."""
    words = [w for w in re.findall(r"\w+", find.lower()) if len(w) > 2]
    low = text.lower()
    hits = [low.find(w) for w in words if low.find(w) >= 0]
    start = max(0, min(hits) - 300) if hits else 0
    return text[start:start + chars]


def _public(url: str) -> bool:
    """Only the open web: never this Mac, the home network or Tailscale (a page could ask for those)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast \
                or ip in ipaddress.ip_network("100.64.0.0/10"):
            return False
    return True


async def _get(url: str) -> str:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": AGENT}) as c:
        try:
            r = await c.get(url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ValueError(type(e).__name__) from e
        if not _public(str(r.url)):                   # a redirect into the home network
            raise ValueError("not a public page")
        return r.text[:2_000_000]


def _outgoing(s: str) -> str:
    """What leaves for the web: never a key, token or password, even if a page talked the model into it."""
    if scrub_text(s, "message").counts:
        raise ValueError("looks like it holds a secret")
    return s


async def search(query: str, k: int = 5) -> list[dict]:
    query = _outgoing(query)
    page = await _get(SEARCH_URL + quote_plus(query[:200]))
    return [{**r, "snippet": scrub_text(r["snippet"], "file").text} for r in parse_results(page, k)]


async def read(url: str, find: str = "") -> str:
    url = _outgoing(url)
    if not await asyncio.to_thread(_public, url):
        raise ValueError("not a public page")
    return scrub_text(around(page_text(await _get(url)), find), "file").text


async def read_full(url: str) -> str:
    """The whole text of a public page (E2: something to teach from), scrubbed. `read` hands the model only a window."""
    url = _outgoing(url)
    if not await asyncio.to_thread(_public, url):
        raise ValueError("not a public page")
    return scrub_text(page_text(await _get(url)).strip(), "file").text
