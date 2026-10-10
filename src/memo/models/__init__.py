"""Canonical schema source (Task 5, Peewee-canonical): Model classes in
memo.models.orm own every CRUD path; the DDL text constants beside them
are the single raw-SQL schema source (FTS5/triggers/CHECKs/CTAS only —
Peewee cannot express those). models/*.py are thin re-exports; schema.sql
is a GENERATED artifact (header line says so) — never hand-edit it.

Import rule: the DDL text is stdlib-free, but memo.models.orm pulls
peewee (hard dep) — db.py already depends on both, so no new cycle.
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
