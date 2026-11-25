"""Step D: a move is one typed, reliable action. Code, not a model clicking. Risk is fixed here and the model cannot change it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

RISKS = ("safe", "ask", "never")


@dataclass
class Outcome:
    ok: bool
    verified: bool = False
    what: str = ""                 # one plain sentence of what happened (or why not)
    undo: dict | None = None       # how to take it back, when possible
    data: dict = field(default_factory=dict)


@dataclass
class Param:
    kind: type = str               # str | int | bool | list (a list of flat objects; `hi` = most items)
    required: bool = True
    max_len: int = 200
    lo: int | None = None
    hi: int | None = None
    choices: tuple | None = None
    doc: str = ""


@dataclass
class Move:
    name: str
    doc: str
    params: dict[str, Param]
    risk: str
    run: Callable[..., Awaitable[Outcome]]                     # run(ctx, params) -> Outcome
    verify: Callable[..., Awaitable[bool]] | None = None       # verify(ctx, params, outcome) -> bool: read back, prove it
    preview: Callable[[dict], str] | None = None               # the exact sentence shown before an `ask` move runs
    undo: Callable[..., Awaitable[Outcome]] | None = None
    # R2: a move brings its own tool-pick lines. `says`: at least 3 ways he would ask for it. `not_mine`: at least 2
    # (line, tool) pairs that look close but belong to another tool. A test fails for a registered move without them.
    no_line: str = "Okay, I didn't do it."      # what she says after a no (a send move says "Okay, not sent.")
    no_text: bool = False          # refuse when the params came from text she read (mail, page, screen): D2 shortcut_run
    says: tuple[str, ...] = ()
    not_mine: tuple[tuple[str, str], ...] = ()
    # check(ctx, params) -> an error the model can fix, or "": runs BEFORE he is asked, so a batch with a bad block is never put to him
    check: Callable[..., str] | None = None

    def __post_init__(self):
        assert self.risk in RISKS, self.risk
        if self.risk == "ask":
            assert self.preview is not None, "an ask move must say exactly what it will do"
