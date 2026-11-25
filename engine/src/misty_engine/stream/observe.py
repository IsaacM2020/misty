"""What Stream shows the brain: one app's controls as a short numbered text list, plus what changed since last time.

The brain picks controls by their number, never by pixel. Numbers are only valid for the observation they came from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .bridge import AppInfo, SnapNode

MAX_NODES = 160          # what the Swift walker is asked for
LIST_PREVIEW = 6         # rows shown from each long list
MAX_SHOWN = 120          # elements the brain sees
VALUE_CHARS = 80

_ROLE = {"AXButton": "button", "AXMenuButton": "menu button", "AXPopUpButton": "pop-up", "AXCheckBox": "checkbox",
         "AXRadioButton": "radio", "AXTextField": "text field", "AXTextArea": "text area", "AXSearchField": "search field",
         "AXComboBox": "combo box", "AXLink": "link", "AXStaticText": "text", "AXRow": "row", "AXCell": "cell",
         "AXTab": "tab", "AXTabGroup": "tabs", "AXSlider": "slider", "AXIncrementor": "stepper", "AXImage": "image",
         "AXWindow": "window", "AXSheet": "sheet", "AXList": "list", "AXTable": "table", "AXOutline": "outline",
         "AXWebArea": "web page", "AXHeading": "heading", "AXGroup": "group", "AXToolbar": "toolbar",
         "AXMenuItem": "menu item", "AXDisclosureTriangle": "disclosure", "AXColorWell": "color well"}
_SKIP_EMPTY = {"AXGroup", "AXToolbar", "AXList", "AXTable", "AXOutline", "AXSplitGroup", "AXScrollArea", "AXLayoutArea"}


# A window's three titlebar buttons by what they do: apps label the green one "this button also has an action to zoom
# the window", and a brain pressed it to minimise and put WhatsApp in full screen.
_WINDOW_BUTTON = {"AXCloseButton": "close window (red)", "AXMinimizeButton": "minimise window (yellow)",
                  "AXZoomButton": "full screen (green)", "AXFullScreenButton": "full screen (green)"}


def name_of(n: SnapNode) -> str:
    if n.subrole in _WINDOW_BUTTON:
        return _WINDOW_BUTTON[n.subrole]
    for v in (n.label, n.desc, n.help, n.ident):
        v = (v or "").strip()
        if v and not v.startswith("_NS:"):              # AppKit's internal ids mean nothing to a brain
            return v
    return ""


@dataclass
class Element:
    id: int
    node: SnapNode

    @property
    def role(self) -> str:
        return _ROLE.get(self.node.role, self.node.role.removeprefix("AX").lower())

    def line(self) -> str:
        n = self.node
        s = f"[{self.id}] {self.role}"
        nm = name_of(n)
        if nm:
            s += f' "{nm[:VALUE_CHARS]}"'
        if n.value and not n.secure and n.value != nm:
            s += f' = "{n.value[:VALUE_CHARS]}"'
        if n.secure:
            s += " (password field)"
        flags = [f for f, on in (("focused", n.focused), ("selected", n.selected)) if on]
        if n.rows > 0:
            flags.append(f"{n.rows} rows")
        if n.region not in ("content", ""):
            flags.append(n.region)
        return s + (f" ({', '.join(flags)})" if flags else "")

    def descriptor(self) -> dict:
        """How a recipe finds this control again later: by what it is, never by number or position."""
        n = self.node
        return {"role": n.role, "name": name_of(n), "region": n.region, "path": list(n.path[-2:])}


@dataclass
class Observation:
    app: AppInfo
    elements: list[Element] = field(default_factory=list)
    hidden: int = 0                         # controls left out to keep the list short

    def get(self, i) -> Element | None:
        try:
            i = int(i)
        except (TypeError, ValueError):
            return None
        return next((e for e in self.elements if e.id == i), None)

    def render(self) -> str:
        head = f"App: {self.app.name} ({self.app.bundle_id})"
        if not self.elements:
            return head + "\nNo controls readable in this app's front window (try look for a screenshot, or open_app)."
        body = "\n".join(e.line() for e in self.elements)
        more = f"\n(+{self.hidden} more controls not shown)" if self.hidden else ""
        return f"{head}\n{body}{more}"

    def keys(self) -> set[tuple]:
        return {(e.node.role, name_of(e.node), e.node.value if not e.node.secure else "") for e in self.elements}


_ROWISH = {"AXRow", "AXCell"}
_ABSORB = {"AXCell", "AXStaticText", "AXImage", "AXTextField"}


def _inside(c: SnapNode, r: SnapNode) -> bool:
    cx, cy = c.frame[0] + c.frame[2] / 2, c.frame[1] + c.frame[3] / 2
    return r.frame[0] <= cx <= r.frame[0] + r.frame[2] and r.frame[1] <= cy <= r.frame[1] + r.frame[3]


def _merge_rows(nodes: list[SnapNode]) -> list[SnapNode]:
    """A table row arrives as row > cell > text (not always next to each other): show it as one row named by the texts
    inside its box, e.g. row "report.pdf | 2.4 MB | Today at 7:40 PM", and drop the pieces."""
    import dataclasses
    used: set[int] = set()
    named: dict[int, SnapNode] = {}
    for i, r in enumerate(nodes):
        if r.role not in _ROWISH or name_of(r) or r.frame[2] <= 0:
            continue
        texts = []
        for j, c in enumerate(nodes):
            if j == i or j in used or c.role not in _ABSORB or not _inside(c, r):
                continue
            t = name_of(c) or c.value
            used.add(j)
            if t and t not in texts:
                texts.append(t)
        if texts:
            named[i] = dataclasses.replace(r, label=" | ".join(texts[:4]))
    return [named.get(i, n) for i, n in enumerate(nodes) if i not in used or i in named]


_HASHY = re.compile(r"^[0-9A-Fa-f]{12,}$")


def _noise(n: SnapNode, prev: SnapNode | None) -> bool:
    nm = name_of(n)
    if n.role == "AXImage" and (not nm or _HASHY.match(nm) or nm.lower() in ("favorite", "favicon")):
        return True                                   # tab favicons and unnamed pictures
    if n.role == "AXStaticText" and prev is not None and nm and nm == name_of(prev):
        return True                                   # a label repeating the control just before it
    return False


def build(app: AppInfo, nodes: list[SnapNode]) -> Observation:
    merged = _merge_rows(nodes)
    merged = [n for i, n in enumerate(merged) if not _noise(n, merged[i - 1] if i else None)]
    kept = [n for n in merged
            if not (n.role in _SKIP_EMPTY and not name_of(n))
            and not (n.role in ("AXCell", "AXImage", "AXSplitter", "AXScrollArea", "AXRadioGroup") and not name_of(n) and not n.value)
            and not (n.frame[2] <= 0 or n.frame[3] <= 0)]
    shown = kept[:MAX_SHOWN]
    return Observation(app, [Element(i + 1, n) for i, n in enumerate(shown)], max(0, len(kept) - len(shown)))


async def observe(hands, app: AppInfo) -> Observation:
    return build(app, await hands.ax_snapshot(app.pid, MAX_NODES, LIST_PREVIEW))


def effect(before: Observation | None, after: Observation) -> str:
    """A short plain line on what an action changed, so the brain (and the done check) can tell a no-op."""
    if before is None:
        return ""
    if before.app.pid != after.app.pid:
        return f"Now in {after.app.name}."
    b, a = before.keys(), after.keys()
    if b == a:
        return "Nothing on screen changed."
    came = [f'{_ROLE.get(r, r)} "{(nm or v)[:40]}"' for r, nm, v in a - b if nm or v][:5]
    went = [f'{_ROLE.get(r, r)} "{(nm or v)[:40]}"' for r, nm, v in b - a if nm or v][:5]
    parts = []
    if came:
        parts.append("appeared or changed: " + ", ".join(came))
    if went:
        parts.append("gone: " + ", ".join(went))
    return "; ".join(parts) or "Something changed."
