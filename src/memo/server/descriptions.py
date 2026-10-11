from __future__ import annotations

from memo.prompts import render

ORDER = ("memory_search", "memory_save", "skill_sync", "memory_delete",
          "skill_get", "skill_put", "skill_update", "skill_install",
          "bootstrap")


def load_descriptions() -> dict:
    return {name: render(f"tool_{name}") for name in ORDER}


DESCRIPTIONS = load_descriptions()
