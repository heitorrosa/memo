from __future__ import annotations

import hashlib
import os
import sys

import numpy as np

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_CACHE = "/models"
BAKED_CACHE = "/opt/memo/models"
DIM = 384

model_singleton = None
force_fallback = False


def load_model():
    from fastembed import TextEmbedding

    cache = next((c for c in (MODEL_CACHE, BAKED_CACHE) if os.path.isdir(c)), None)
    return TextEmbedding(MODEL_NAME, cache_dir=cache)


def fallback_vectors(texts: list[str]) -> list[bytes]:
    out = []
    for text in texts:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "little")
        generator = np.random.RandomState(seed)
        vector = generator.rand(DIM).astype(np.float32) - 0.5
        vector /= np.linalg.norm(vector) + 1e-12
        out.append(vector.tobytes())
    return out


def embed(texts: list[str]) -> list[bytes]:
    global model_singleton
    if force_fallback:
        return fallback_vectors(texts)
    if model_singleton is None:
        print("model-download-start", file=sys.stderr)
        try:
            model_singleton = load_model()
        except Exception as exc:
            raise RuntimeError(
                f"internal: embedding model unavailable offline ({exc})"
            ) from exc
        print("model-load done", file=sys.stderr)
    return [np.asarray(vector, dtype=np.float32).tobytes() for vector in model_singleton.embed(texts)]


def cosine(first: bytes, second: bytes) -> float:
    first_array = np.frombuffer(first, dtype=np.float32)
    second_array = np.frombuffer(second, dtype=np.float32)
    return float(
        np.dot(first_array, second_array) / (np.linalg.norm(first_array) * np.linalg.norm(second_array) + 1e-12)
    )
