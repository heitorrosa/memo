"""Canonical schema source (Task 5): per-domain DDL lives in this package;
schema.sql is a GENERATED artifact (header line says so) — never hand-edit it.

Import rule: models/ imports NOTHING from memo (stdlib-free, pure strings),
so db.py can read render_schema() with zero import-cycle risk.
"""
from memo.models.memories import MEMORIES_DDL
from memo.models.meta import META_DDL
from memo.models.skills import SKILLS_DDL

HEADER: str = ("-- GENERATED from memo.models: do not hand-edit. "
               "Edit src/memo/models/*.py, then run `python -m memo.models`.")


def render_schema() -> str:
    return HEADER + "\n" + META_DDL + "\n\n" + MEMORIES_DDL + "\n" + SKILLS_DDL + "\n"


__all__ = [
    "HEADER",
    "MEMORIES_DDL",
    "META_DDL",
    "SKILLS_DDL",
    "render_schema",
]
