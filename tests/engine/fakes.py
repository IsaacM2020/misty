import keyring
import keyring.backend
import keyring.errors


class InMemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        try:
            del self.store[(service, username)]
        except KeyError:
            raise keyring.errors.PasswordDeleteError("not found")


import hashlib
from pathlib import Path

import numpy as np


class FakeEmbedder:
    """Char-trigram hashing into `dim` buckets, L2-normalised. Similar strings give close vectors."""

    def __init__(self, dim: int = 768):
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        t = f"  {text.lower()}  "
        for i in range(len(t) - 2):
            h = int.from_bytes(hashlib.blake2b(t[i:i + 3].encode(), digest_size=4).digest(), "little")
            v[h % self.dim] += 1.0
        n = float(np.linalg.norm(v))
        if n == 0:
            v[0] = 1.0
            n = 1.0
        return v / n

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)

    def embed_images(self, paths: list[Path]) -> np.ndarray:
        if not paths:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._vec(Path(p).name) for p in paths])


# ---- Q1: stands in for ApiRunner everywhere a test used to inject a ClaudeRunner
class ScriptedRunner:
    """Stands in for ApiRunner. `script` is a list of dicts or callables(system, parts, purpose) -> dict.

    Same prompt guard as the real thing (secret-shaped text is refused before a "call"), no network.
    A schema keyed answer is also supported: `set(schema, value_or_callable(prompt))` as the old fake had.
    """

    def __init__(self, *script):
        self.script, self.calls = list(script), []
        self.responses: dict = {}
        self._raise: list = []

    def set(self, schema: str, value) -> None:
        self.responses[schema] = value

    def raise_once(self, exc) -> None:
        self._raise.append(exc)

    @property
    def prompts(self) -> list[str]:
        return [c["system"].strip() + "\n\n" + "\n\n".join(c["parts"]) for c in self.calls]

    @property
    def model_keys(self) -> list[str]:
        return [c["model_key"] for c in self.calls]

    @property
    def schemas(self) -> list[str]:
        return [c["schema"] for c in self.calls]

    async def run(self, system, parts, schema_name, *, purpose, model_key="haiku", max_tokens=1500,
                  job_id=None, build_id=None, phase=None, accept=None):
        import json
        from misty_engine.errors import PrivacyViolation
        from misty_engine.runner import RunResult
        from misty_engine.scrub import find_secrets
        prompt = system.strip() + "\n\n" + "\n\n".join(parts)
        if find_secrets(prompt, "file"):
            raise PrivacyViolation("UNSCRUBBED_CONTENT")
        if self._raise:
            raise self._raise.pop(0)
        self.calls.append({"system": system, "parts": list(parts), "schema": schema_name, "purpose": purpose,
                           "model_key": model_key, "max_tokens": max_tokens, "build_id": build_id, "phase": phase})
        if self.script:
            nxt = self.script.pop(0)
        else:
            nxt = self.responses.get(schema_name, {})
        data = nxt(prompt) if callable(nxt) else nxt
        return RunResult(text=json.dumps(data), json=data, usage={}, cost_usd=0.0)
