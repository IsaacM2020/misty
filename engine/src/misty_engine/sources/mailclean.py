"""Turn a raw mail body into the text worth indexing: no quotes, signatures or footers."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

_QUOTE_START = [
    re.compile(r"^On .{5,200}wrote:\s*$", re.I),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.I),
    re.compile(r"^-{2,}\s*Forwarded message\s*-{2,}", re.I),
    re.compile(r"^_{5,}\s*$"),
    re.compile(r"^From:\s.+", re.I),             # Outlook header block ("From: x / Sent: y / To: z")
]
_SIG = [re.compile(r"^-- ?$"), re.compile(r"^Sent from my .+", re.I), re.compile(r"^Get Outlook for .+", re.I),
        re.compile(r"^Sent via .+", re.I)]
_FOOTER = re.compile(r"unsubscribe|view (this )?(email )?in (your )?browser|manage (your )?preferences|"
                     r"you are receiving this|you're receiving this|update your email preferences", re.I)


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "head"]):
        t.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for blk in soup.find_all(["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"]):
        blk.append("\n")
    return soup.get_text()


def clean_mail(body: str, is_html: bool) -> str:
    text = _html_to_text(body) if is_html else body
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    out: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if any(p.match(s) for p in _QUOTE_START) and out is not None:
            # a quoted-reply header: everything after belongs to the quoted thread
            if s.lower().startswith("from:") and not _looks_like_header_block(text, line):
                out.append(line.rstrip())
                continue
            break
        if any(p.match(s) for p in _SIG):
            break
        if s.startswith(">"):
            continue
        if _FOOTER.search(s):
            continue
        out.append(line.rstrip())
    cleaned = re.sub(r"\n{2,}", "\n", "\n".join(out)).strip()
    return cleaned


def _looks_like_header_block(text: str, line: str) -> bool:
    """`From:` only starts a quote block when `Sent:`/`Date:` follows within a few lines."""
    lines = text.split("\n")
    try:
        i = lines.index(line)
    except ValueError:
        return False
    return any(re.match(r"^(Sent|Date|To|Subject):", l.strip(), re.I) for l in lines[i + 1:i + 4])
