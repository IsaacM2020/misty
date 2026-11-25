"""Every Stream run as one JSONL file: the training data for Isaac's own model later. Local only (it holds screen text)."""
from __future__ import annotations

import json
import time
from pathlib import Path


class RunLog:
    def __init__(self, home: Path, run_id: str):
        self.path = home / "stream" / "runs" / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, **data) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "kind": kind, **data}, default=str) + "\n")
