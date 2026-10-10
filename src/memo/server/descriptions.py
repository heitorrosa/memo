"""Tool/prompt descriptions (§§14/8/21 — they ARE the control plane)."""
from __future__ import annotations

import json
import pathlib


def load_descriptions() -> dict:
    """Loaded once from descriptions.json; wording is byte-identical
    to the former DESC_* literals.
    """
    path = pathlib.Path(__file__).with_name("descriptions.json")
    return json.loads(path.read_text(encoding="utf-8"))


DESCRIPTIONS = load_descriptions()
