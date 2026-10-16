from __future__ import annotations

import os
import sqlite3
import tempfile

from peewee import SqliteDatabase

database = SqliteDatabase(None)


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


from memo.models.agents import Agent, AgentState
from memo.models.memories import (
    ArchivedMemory,
    Edge,
    Episode,
    Memory,
    MemoryFTS,
)
from memo.models.meta import MemoMeta
from memo.models.skills import Skill, utc_now

HEADER: str = ("-- GENERATED from memo.models: do not hand-edit. "
               "Edit src/memo/models/{memories,skills,meta,agents}.py, then run `python -m memo.models`.")

CREATE_MODELS = [MemoMeta, Memory, Episode, Skill, MemoryFTS, Agent, AgentState,
                 Edge]

MODELS = [MemoMeta, Memory, MemoryFTS, ArchivedMemory, Episode, Skill,
          Agent, AgentState, Edge]


def render_schema() -> str:
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    try:
        scratch = SqliteDatabase(path)
        with scratch.bind_ctx(CREATE_MODELS):
            scratch.create_tables(CREATE_MODELS)
        scratch.close()
        raw = sqlite3.connect(path)
        try:
            raw.executescript(RAW_TAIL)
            rows = list(raw.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
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
