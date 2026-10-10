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
