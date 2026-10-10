"""Central LLM-facing prompt store (text lives in prompts.json)."""
from __future__ import annotations

import json
import pathlib


def load_prompts() -> dict:
    path = pathlib.Path(__file__).with_name("prompts.json")
    return json.loads(path.read_text(encoding="utf-8"))


PROMPTS = load_prompts()


def render(prompt_key: str, **fields) -> str:
    """Fill one stored prompt's {placeholders}. Unknown key/field raises.

    The parameter is named prompt_key (not key) so it can never collide
    with a {key} placeholder in the store's templates.
    """
    return PROMPTS[prompt_key].format(**fields)
