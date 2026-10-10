"""Peewee ORM entry point: glue ONLY (spec §5).

Per-table Model modules own every table shape (memories/skills/meta);
this module keeps the shared deferred database handle, the RAW_TAIL
triggers/clone (Peewee cannot express triggers or CTAS), the
creation-order lists, render_schema(), and model re-exports so the
memo.models.orm import path stays stable.

Deferred SqliteDatabase: db.py init()s it per path, so tests can bind
a fresh tmp file each (Peewee test pattern) with zero simultaneous-DB
fights — every fixture gets its own database object state via init().
Embeddings stay raw BLOB bytes (embeddings.py owns encode/cosine).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from peewee import SqliteDatabase

database = SqliteDatabase(None)


# Raw tail: FTS sync triggers + archived clone. Peewee cannot express
# triggers or CTAS, so this fragment stays raw SQL — applied by
# db.migrate AFTER create_tables, and included in render_schema().
RAW_TAIL: str = (
    "CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN\n"
    "  INSERT INTO memories_fts(rowid, key, value) VALUES (new.rowid, new.key, new.value);\n"
    "END;\n"
    "CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN\n"
    "  INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.rowid, old.key, old.value);\n"
    "END;\n"
    "CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN\n"
    "  INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.rowid, old.key, old.value);\n"
    "  INSERT INTO memories_fts(rowid, key, value) VALUES (new.rowid, new.key, new.value);\n"
    "END;\n"
    "CREATE TABLE IF NOT EXISTS archived_memories AS SELECT *, '' AS archived_at, '' AS archived_reason FROM memories LIMIT 0;"
)


# Per-table Models (canonical owners). Imported AFTER database: these
# modules bind `from memo.models.orm import database`, which resolves
# against this partially-initialized module — safe because database is
# defined above these imports, and memo.models.__init__ always runs
# this module before any per-table submodule import.
from memo.models.agents import Agent, AgentState
from memo.models.memories import ArchivedMemory, Episode, Memory, MemoryFTS
from memo.models.meta import MemoMeta
from memo.models.skills import Skill, utc_now

HEADER: str = ("-- GENERATED from memo.models: do not hand-edit. "
               "Edit src/memo/models/{memories,skills,meta,agents}.py, then run `python -m memo.models`.")

# Tables owned by create_tables (creation order: parents before FTS child).
CREATE_MODELS = [MemoMeta, Memory, Episode, Skill, MemoryFTS, Agent, AgentState]

MODELS = [MemoMeta, Memory, MemoryFTS, ArchivedMemory, Episode, Skill,
          Agent, AgentState]


def render_schema() -> str:
    """Build the full schema text from Models: create_tables on a scratch
    DB, apply the raw tail, dump sqlite_master in creation order."""
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    try:
        scratch = SqliteDatabase(path)
        with scratch.bind_ctx(CREATE_MODELS):
            scratch.create_tables(CREATE_MODELS)
        scratch.close()
        # raw: trigger bodies contain ";" so executescript (stdlib-only
        # multi-statement runner) applies the tail, not peewee.
        raw = sqlite3.connect(path)
        try:
            raw.executescript(RAW_TAIL)
            rows = list(raw.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
                # FTS5 shadow tables are auto-created index bytes, not
                # schema: the old artifact never carried them either.
                " AND name NOT IN ('memories_fts_data','memories_fts_idx',"
                "'memories_fts_docsize','memories_fts_config')"
                " ORDER BY rowid").fetchall())
        finally:
            raw.close()
        body = "\n".join(sql.rstrip() + ";" for (sql,) in rows)
        return HEADER + "\n" + body + "\n"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


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
    "Agent",
    "AgentState",
    "database",
    "render_schema",
    "utc_now",
]
