import json
from pathlib import Path

import pytest

from misty_engine.sources.graphify import GraphHints


@pytest.fixture
def graph_json(tmp_path):
    g = {
        "nodes": [
            {"id": "a", "label": "server.py", "community_name": "Bridge"},
            {"id": "b", "label": "n8n", "community_name": "Infra"},
            {"id": "c", "label": "Raspberry Pi", "community_name": "Infra"},
            {"id": "d", "label": "infer.py", "community_name": "Memory"},
            {"id": "e", "label": "Lonely", "community_name": "Misc"},
        ],
        "links": [
            {"source": "a", "target": "b", "weight": 1},
            {"source": "c", "target": "a", "weight": 0.5},
            {"source": "d", "target": "d2-missing", "weight": 1},
        ],
        "hyperedges": [{"id": "h", "nodes": ["a", "b", "d"], "label": "stack"}],
    }
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(g))
    return p


def test_related_neighbours_are_low_weight(graph_json):
    h = GraphHints.load(graph_json)
    rel = dict(h.related("SERVER.py"))              # case-insensitive
    assert set(rel) == {"n8n", "Raspberry Pi", "infer.py"}
    assert all(0 < w <= 0.3 for w in rel.values())
    assert rel["n8n"] >= rel["Raspberry Pi"]


def test_unknown_and_isolated_labels(graph_json):
    h = GraphHints.load(graph_json)
    assert h.related("nothing here") == [] and h.related("Lonely") == []


def test_community_lookup(graph_json):
    assert GraphHints.load(graph_json).community("n8n") == "Infra"


def test_missing_file_is_empty(tmp_path):
    h = GraphHints.load(tmp_path / "nope.json")
    assert h.related("x") == []


@pytest.mark.live
def test_real_graph_loads():
    p = Path.home() / "Notes/.graphify/graph.json"
    h = GraphHints.load(p)
    assert len(h.labels()) > 100
