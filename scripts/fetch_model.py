from __future__ import annotations

import os
from pathlib import Path

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
