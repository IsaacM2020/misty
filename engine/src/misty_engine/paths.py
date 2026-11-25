"""Where Misty keeps things on disk."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MistyPaths:
    home: Path

    @property
    def wiki(self) -> Path: return self.home / "wiki"
    @property
    def db(self) -> Path: return self.home / "misty.db"
    @property
    def logs(self) -> Path: return self.home / "logs"
    @property
    def run(self) -> Path: return self.home / "run"
    @property
    def sock(self) -> Path: return self.run / "engine.sock"
    @property
    def tmp(self) -> Path: return self.home / "tmp"
    @property
    def models(self) -> Path: return self.home / "models"

    @classmethod
    def from_env(cls) -> "MistyPaths":
        return cls(Path(os.environ.get("MISTY_HOME") or Path.home() / "Misty"))

    def ensure(self) -> None:
        for d in (self.home, self.logs, self.run, self.tmp):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
