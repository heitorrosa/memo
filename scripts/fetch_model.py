"""Fetch the MiniLM ONNX embedding weights into ./models once (gitignored).

Run this on a fresh clone before `docker compose up -d --build`:

    python scripts/fetch_model.py   # ~90 MB, one time

The server loads `sentence-transformers/all-MiniLM-L6-v2` through fastembed,
which reads its cache from FASTEMBED_CACHE_PATH (=/models in docker-compose.yml).
Populating ./models here means builds and container recreates never re-download
the weights from HuggingFace again.

Files are COPIED, not symlinked (HF_HUB_DISABLE_SYMLINKS), so a cache populated
on Windows stays valid when bind-mounted into the Linux container.
"""
from __future__ import annotations

import os
from pathlib import Path

# Must precede the fastembed import: fastembed pulls in huggingface_hub at module
# load, and huggingface_hub reads this flag once, at import time.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def main() -> int:
    from fastembed import TextEmbedding

    print(f"fetching {MODEL_NAME} into {MODELS} ...")
    TextEmbedding(MODEL_NAME, cache_dir=str(MODELS))
    print(f"model ready in {MODELS} (mounted read-only at /models by compose)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
