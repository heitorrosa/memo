"""Lazy fastembed singleton (spec §24, F-01). Never loads at import."""

from __future__ import annotations

import hashlib
import os
import sys

import numpy as np

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384

_model = None
# Test/CI hook: deterministic hash vectors, no model download (Q-03 brute-force-first).
force_fallback = False


def _load_model():
    from fastembed import TextEmbedding

    return TextEmbedding(MODEL_NAME)


def _fallback(texts: list[str]) -> list[bytes]:
    out = []
    for t in texts:
        seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:4], "little")
        rng = np.random.RandomState(seed)
        v = rng.rand(DIM).astype(np.float32) - 0.5
        v /= np.linalg.norm(v) + 1e-12
        out.append(v.tobytes())
    return out


def embed(texts: list[str]) -> list[bytes]:
    """Embed to 384-float32 BLOBs. Offline model fetch -> RuntimeError (F-01)."""
    global _model
    if force_fallback or os.getenv("MEMO_EMBEDDINGS_FALLBACK") == "1":
        return _fallback(texts)
    if _model is None:
        print("model-download-start", file=sys.stderr)
        try:
            _model = _load_model()
        except Exception as exc:
            raise RuntimeError(
                f"internal: embedding model unavailable offline ({exc})"
            ) from exc
        print("model-load done", file=sys.stderr)
    return [np.asarray(v, dtype=np.float32).tobytes() for v in _model.embed(texts)]


def cosine(a: bytes, b: bytes) -> float:
    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    return float(
        np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-12)
    )
