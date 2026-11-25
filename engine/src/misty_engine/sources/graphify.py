"""graphify graph.json: low-weight linking hints only (the graph is stale, so it never decides anything)."""
from __future__ import annotations

import json
from pathlib import Path

HINT_WEIGHT = 0.3          # scale so a graphify hint can never outweigh a real signal
HYPEREDGE_FACTOR = 0.5


class GraphHints:
    def __init__(self, nodes: dict[str, dict], links: list[dict], hyperedges: list[dict]):
        self._nodes = nodes
        self._by_label = {n.get("label", "").lower(): nid for nid, n in nodes.items() if n.get("label")}
        self._adj: dict[str, dict[str, float]] = {}

        def add(a: str, b: str, w: float) -> None:
            if a in nodes and b in nodes and a != b:
                self._adj.setdefault(a, {})
                self._adj[a][b] = max(self._adj[a].get(b, 0.0), w)

        for l in links:
            w = min(float(l.get("weight", 1) or 1), 1.0)
            add(l.get("source"), l.get("target"), w)
            add(l.get("target"), l.get("source"), w)
        for h in hyperedges:
            members = h.get("nodes", [])
            for a in members:
                for b in members:
                    add(a, b, HYPEREDGE_FACTOR)

    @classmethod
    def load(cls, path: Path) -> "GraphHints":
        try:
            g = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return cls({}, [], [])
        nodes = {n["id"]: n for n in g.get("nodes", []) if "id" in n}
        return cls(nodes, g.get("links", []), g.get("hyperedges", []))

    def labels(self) -> list[str]:
        return [n["label"] for n in self._nodes.values() if n.get("label")]

    def related(self, label: str) -> list[tuple[str, float]]:
        nid = self._by_label.get(label.lower())
        if nid is None:
            return []
        out = [(self._nodes[o].get("label", o), w * HINT_WEIGHT) for o, w in self._adj.get(nid, {}).items()]
        return sorted(out, key=lambda t: (-t[1], t[0]))

    def community(self, label: str) -> str | None:
        nid = self._by_label.get(label.lower())
        return self._nodes[nid].get("community_name") if nid else None
