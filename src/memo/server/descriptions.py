"""Tool/prompt descriptions (they ARE the control plane).

Text lives in memo/prompts.json (tool_* keys); this module is a thin
read-through so existing imports keep working.
"""
from __future__ import annotations

from memo.prompts import PROMPTS, render

_ORDER = ("memory_search", "memory_save", "skill_sync", "memory_delete",
          "skill_get", "skill_put", "skill_create", "skill_update",
          "skill_install", "bootstrap")


def load_descriptions() -> dict:
    """Loaded once from the prompts store; keys match the former JSON file.

    render() collapses the escaped literal braces (``{{ }}``) so the LLM
    sees clean receipt shapes like ``{key, action}`` instead of the raw
    ``{{key, action}}`` stored in prompts.json.
    """
    return {name: render(f"tool_{name}") for name in _ORDER}


DESCRIPTIONS = load_descriptions()
