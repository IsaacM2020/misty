"""Chat's web lookups: DuckDuckGo results parsed, the useful part of a page kept, never a local address."""
from __future__ import annotations

import pytest

from misty_engine import web

PAGE = '''<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FSpeedcubing&amp;rut=x">
Speedcubing - <b>Wikipedia</b></a><a class="result__snippet" href="#">The 3x3x3 <b>single</b> record is 2.51 s.</a></div>'''


def test_results_are_parsed_with_their_real_links():
    r = web.parse_results(PAGE)
    assert r == [{"title": "Speedcubing - Wikipedia", "url": "https://en.wikipedia.org/wiki/Speedcubing",
                  "snippet": "The 3x3x3 single record is 2.51 s."}]


def test_a_read_jumps_to_the_words_asked_about():
    text = "intro " * 2000 + "3x3x3 Cube Single 2.51 Xuanyi Geng" + " tail" * 100
    part = web.around(web.page_text(f"<html><script>x()</script><p>{text}</p></html>"), "3x3x3 single")
    assert "2.51 Xuanyi Geng" in part and len(part) <= web.PAGE_CHARS


@pytest.mark.asyncio
async def test_never_reads_this_mac_or_the_home_network():
    for url in ("http://127.0.0.1:5678/", "http://localhost/", "http://192.168.1.43/", "http://100.103.216.19:5678",
                "file:///etc/passwd"):
        with pytest.raises(ValueError):
            await web.read(url)


@pytest.mark.asyncio
async def test_a_secret_never_goes_out_in_a_search():
    with pytest.raises(ValueError):
        await web.search("sk-or-v1-" + "a1b2c3d4" * 8)
