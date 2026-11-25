"""Fallback: per-chat .txt exports from WhatsApp ("Export chat"). No unofficial libraries."""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from ..types import RawItem

# [14/10/2026, 21:03:11] Name: text        (iOS)
# 14/10/2026, 9:03 pm - Name: text         (Android)
_LINE = re.compile(
    r"^\[?(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{2,4}),?\s+"
    r"(?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?\s*(?P<ap>[AaPp]\.?[Mm]\.?)?\]?\s*(?:-\s*)?(?P<rest>.*)$")
_MARKS = dict.fromkeys(map(ord, "‎‏‪‫‬﻿"), None)


@dataclass(frozen=True)
class ChatMessage:
    ts: str
    sender: str
    text: str


def _clean(line: str) -> str:
    return line.translate(_MARKS).replace(" ", " ").replace(" ", " ").rstrip("\r\n")


def _ts(m: re.Match) -> str:
    y = int(m["y"])
    y += 2000 if y < 100 else 0
    h = int(m["H"])
    ap = (m["ap"] or "").lower().replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return datetime(y, int(m["m"]), int(m["d"]), h, int(m["M"]), int(m["S"] or 0)).strftime("%Y-%m-%dT%H:%M:%S")


def parse_export(path: Path) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    cur: list | None = None        # [ts, sender, text] of the message being built, or None after a system line
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = _clean(raw)
        m = _LINE.match(line)
        if m:
            try:
                ts = _ts(m)
            except ValueError:
                m = None
        if m:
            if cur:
                out.append(ChatMessage(cur[0], cur[1], cur[2]))
            sender, sep, text = m["rest"].partition(": ")
            cur = [ts, sender.strip(), text] if sep else None      # no "Name: " means a system line
        elif cur is not None and line:
            cur[2] += "\n" + line
    if cur:
        out.append(ChatMessage(cur[0], cur[1], cur[2]))
    return [m for m in out if m.text.strip()]


def _week(ts: str) -> str:
    y, w, _ = datetime.fromisoformat(ts).isocalendar()
    return f"{y}-W{w:02d}"


class ExportSource:
    name = "whatsapp_export"
    item_sources = ("whatsapp_export",)

    def __init__(self, paths: list[Path]):
        self.paths = [Path(p) for p in paths]
        self.stats: Counter = Counter()

    def next_cursor(self) -> str | None:
        return None

    def discover(self, cursor: str | None = None, limit: int | None = None) -> Iterator[RawItem]:
        n = 0
        for p in self.paths:
            if not p.is_file():
                self.stats["missing"] += 1
                continue
            weeks: dict[str, int] = defaultdict(int)
            for msg in parse_export(p):
                weeks[_week(msg.ts)] += 1
            st = p.stat()
            h = hashlib.sha256(str(p).encode()).hexdigest()[:12]
            for week, count in sorted(weeks.items()):
                if limit is not None and n >= limit:
                    return
                n += 1
                yield RawItem(source=self.name, kind="chat_week", locator=f"whatsapp-export:{h}:{week}",
                              title=f"{p.stem} · {week}",
                              meta={"path": str(p), "week": week, "rev": f"{st.st_size}:{int(st.st_mtime)}",
                                    "size": st.st_size, "mtime": st.st_mtime})

    def load_text(self, item: RawItem) -> str:
        week = item.meta["week"]
        lines = []
        for msg in parse_export(Path(item.meta["path"])):
            if _week(msg.ts) == week:
                lines.append(f"{msg.ts[:10]} {msg.ts[11:16]} {msg.sender}: {msg.text}")
        return "\n".join(lines)
