from __future__ import annotations

import json
import pathlib


def load_prompts() -> dict:
    path = pathlib.Path(__file__).with_name("prompts.json")
    return json.loads(path.read_text(encoding="utf-8"))


PROMPTS = load_prompts()


def render(prompt_key: str, **fields) -> str:
    return PROMPTS[prompt_key].format(**fields)
