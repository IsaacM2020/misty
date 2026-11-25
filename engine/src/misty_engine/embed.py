"""Local embeddings: EmbeddingGemma 2 on MLX. The model loads once, on first use, and stays loaded.

Text and images share one 768-d space, so a text query can find a photo.
"""
from __future__ import annotations

import gc
import threading
import time
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import EmbeddingCfg, Pinned


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...
    def embed_images(self, paths: list[Path]) -> np.ndarray: ...


PAD_MULTIPLE = 64                       # fixed shape buckets: MLX slows down steadily on ever-new shapes
CACHE_LIMIT_BYTES = 512 * 1024 * 1024   # measured: 40 -> 112 ms per call over 3k calls without these two
IDLE_CLEAR_SECONDS = 20                 # no embedding for this long: hand the scratch buffers (up to 512 MB) back
VISION_KEEP_SECONDS = 300               # the photo tower stays loaded this long after the last photo
# The checkpoint carries a speech tower (610 MB) and a photo tower (335 MB) next to the 540 MB text model. Text vectors
# never touch them: measured bit-identical with both dropped, 1.5 GB -> 0.55 GB resident.
TOWERS = ("audio_tower", "embed_audio")
VISION = ("vision_tower", "embed_vision")


def _normalise(a: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    return (a / np.maximum(n, 1e-12)).astype(np.float32)


class GemmaEmbedder:
    def __init__(self, cfg: EmbeddingCfg):
        self.cfg = cfg
        self.dim = cfg.dim
        self._model = None
        self._proc = None
        self._has_vision = False
        self._last_image = 0.0
        self._idle: threading.Timer | None = None
        self._lock = threading.Lock()       # MLX models are not thread-safe

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure(self) -> None:
        if self._model is not None:
            return
        import mlx.core as mx
        from mlx_vlm import load
        mx.set_cache_limit(CACHE_LIMIT_BYTES)
        self._model, self._proc = load(self.cfg.model_id, revision=self.cfg.revision or None)
        self._drop(TOWERS + VISION)
        self._has_vision = False

    def _after_use(self) -> None:
        """Called with the lock held after every embedding: restart the idle countdown."""
        if self._idle is not None:
            self._idle.cancel()
        self._idle = threading.Timer(IDLE_CLEAR_SECONDS, self._when_idle)
        self._idle.daemon = True
        self._idle.start()

    def _when_idle(self) -> None:
        import mlx.core as mx
        with self._lock:
            self._trim_vision_if_idle()
            gc.collect()
            mx.clear_cache()

    def _drop(self, names: tuple[str, ...]) -> None:
        import mlx.core as mx
        for n in names:
            if n in self._model:
                self._model[n] = None       # the weights go with the reference
        gc.collect()
        mx.clear_cache()

    def _ensure_vision(self) -> None:
        """Photos need the vision tower: reload the checkpoint with it (a couple of seconds), drop the speech tower."""
        if self._has_vision:
            return
        import mlx.core as mx
        from mlx_vlm import load
        self._model, self._proc = load(self.cfg.model_id, revision=self.cfg.revision or None)
        self._drop(TOWERS)
        self._has_vision = True
        mx.set_cache_limit(CACHE_LIMIT_BYTES)

    def _trim_vision_if_idle(self) -> None:
        if self._has_vision and time.monotonic() - self._last_image > VISION_KEEP_SECONDS:
            self._drop(VISION)
            self._has_vision = False

    def _run(self, inputs: dict) -> np.ndarray:
        import mlx.core as mx
        out = self._model(**{k: mx.array(v) for k, v in inputs.items()}).text_embeds
        mx.eval(out)
        return np.array(out.astype(mx.float32))

    def _embed_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))     # less padding per batch
        with self._lock:
            self._ensure()
            tok = self._proc.tokenizer
            for s in range(0, len(order), self.cfg.batch):
                idx = order[s:s + self.cfg.batch]
                enc = tok([texts[i] for i in idx], padding=True, truncation=True,
                          max_length=self.cfg.max_len, pad_to_multiple_of=PAD_MULTIPLE,
                          return_tensors="np")
                out[idx] = self._run(dict(enc))
            self._trim_vision_if_idle()
            self._after_use()
        return _normalise(out)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed_texts([self.cfg.doc_prefix + t for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed_texts([self.cfg.query_prefix + text])[0]

    def embed_images(self, paths: list[Path]) -> np.ndarray:
        if not paths:
            return np.zeros((0, self.dim), dtype=np.float32)
        from PIL import Image
        import pillow_heif
        pillow_heif.register_heif_opener()
        vecs = []
        with self._lock:
            self._ensure()
            self._ensure_vision()
            self._last_image = time.monotonic()
            for p in paths:
                with Image.open(p) as im:
                    rgb = im.convert("RGB")
                enc = self._proc(images=[rgb], return_tensors="np")
                vecs.append(self._run(dict(enc))[0])
            self._last_image = time.monotonic()
            self._after_use()
        return _normalise(np.stack(vecs))


def load_embedder(pinned: Pinned) -> GemmaEmbedder:
    if pinned.embedding.backend != "mlx":
        raise ValueError("EMBED_BACKEND")
    return GemmaEmbedder(pinned.embedding)
