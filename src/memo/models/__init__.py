"""Schema entry point: re-exports the Peewee Models (plus render_schema)
from memo.models.orm — no DDL strings anywhere. `python -m memo.models`
regenerates the schema.sql artifact from these same Models.

Migration note: schema.sql's pre-Peewee text form is gone; the v3 schema
it describes is equivalent (same tables/columns/CHECKs/DEFAULTs/FTS/
triggers — index names are now Peewee-generated). v3→v3 upgrades need
no data migration: migrate() is idempotent (safe create_tables +
IF NOT EXISTS tail), and old idx_memories_* names stay valid on DBs
created before the migration.
"""
from memo.models import orm as orm_models
from memo.models.orm import (
    HEADER,
    CREATE_MODELS,
    MODELS,
    RAW_TAIL,
    ArchivedMemory,
    Episode,
    MemoMeta,
    Memory,
    MemoryFTS,
    Skill,
    database,
    render_schema,
    utc_now,
)

__all__ = [
    "HEADER",
    "RAW_TAIL",
    "CREATE_MODELS",
    "MODELS",
    "MemoMeta",
    "Memory",
    "MemoryFTS",
    "ArchivedMemory",
    "Episode",
    "Skill",
    "database",
    "orm_models",
    "render_schema",
    "utc_now",
]
