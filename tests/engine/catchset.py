"""G1: the 50 made-up worked solutions in tests/engine/fixtures/catch/ and their labels. Used by the G2, G3, G4 and G6 tests.

A file is how his working looks as text from a page: furniture lines, the question lines, then the working lines (one per line),
then furniture again. `labels.yaml` says where the working starts (`working_from`, a physical 1-based line) and what is wrong.
Line numbers in `expect.line` count from the first working line, 1-based. Furniture never sits between working lines.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DIR = Path(__file__).resolve().parent / "fixtures" / "catch"
FURNITURE = {"Untitled document", "Share", "Saved to Drive", "File  Edit  View  Insert  Format  Tools", "Comments", "Editing", "100%",
             "Maths homework", "Page 1 of 1", "Word count: 38", "Suggesting", "Help"}
PROVABLE_KINDS = ("sign", "arithmetic", "copied")
KINDS = PROVABLE_KINDS + ("unit", "formula")


@dataclass(frozen=True)
class Case:
    id: str
    subject: str
    text: str
    lines: tuple[str, ...]
    working_from: int          # physical 1-based line number of the first working line
    expect: dict | None        # {"line": n (from the first working line), "kind": k} or None
    rough: bool
    provable: bool
    data: bool                 # the question carries a data line the working copies numbers from
    note: str

    @property
    def question(self) -> list[str]:
        return [ln for ln in self.lines[: self.working_from - 1] if ln not in FURNITURE]

    @property
    def working(self) -> list[str]:
        """The working lines only, furniture at the bottom left out. working[0] is line 1."""
        rest = self.lines[self.working_from - 1:]
        return [ln for ln in rest if ln not in FURNITURE]

    @property
    def error_text(self) -> str | None:
        return self.working[self.expect["line"] - 1] if self.expect else None


def load() -> list[Case]:
    out = []
    for row in yaml.safe_load((DIR / "labels.yaml").read_text()):
        text = (DIR / f"{row['id']}.txt").read_text()
        out.append(Case(row["id"], row["subject"], text, tuple(text.splitlines()), row["working_from"],
                        None if row["expect"] == "none" else dict(row["expect"]), bool(row["rough"]), bool(row["provable"]), bool(row["data"]), row["note"]))
    return out


def split(cases: list[Case] | None = None) -> dict[str, list[Case]]:
    cases = cases if cases is not None else load()
    return {"error": [c for c in cases if c.expect], "correct": [c for c in cases if not c.expect and not c.rough], "rough": [c for c in cases if not c.expect and c.rough]}
