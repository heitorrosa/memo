"""Peewee ORM models: THE schema definition (spec §5, Peewee-canonical).

Model classes below own every CRUD path. The DDL text constants beside
them are the single raw-SQL schema source — kept ONLY because Peewee
cannot express FTS5 virtual tables, ai/ad/au sync triggers, CHECKs, or
the archived_memories CTAS clone. models/*.py are thin re-exports of
these constants; render_schema()/schema.sql/migrate() consume them
verbatim, and test_models_drift guards Models-vs-migrated-schema parity.

Deferred SqliteDatabase: db.py init()s it per path, so tests can bind
a fresh tmp file each (Peewee test pattern) with zero simultaneous-DB
fights — every fixture gets its own database object state via init().
CHECKs stay in DDL (Python never re-validates them). FTS sync stays in
the ai/ad/au triggers created by the DDL; MemoryFTS only declares the
external-content mapping (content='memories', content_rowid='rowid') so
match()/rank()/bm25() queries hit the same index bytes.
Embeddings stay raw BLOB bytes (embeddings.py owns encode/cosine).
"""
from __future__ import annotations

import datetime

from peewee import (
    AutoField,
    BlobField,
    CompositeKey,
    FloatField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)
from playhouse.sqlite_ext import FTS5Model, SearchField

database = SqliteDatabase(None)


# Canonical DDL text (single owner; models/*.py re-export these verbatim).
META_DDL: str = (
    "CREATE TABLE IF NOT EXISTS memo_meta(\n"
    "  k TEXT PRIMARY KEY, v TEXT NOT NULL);"
)

MEMORIES_DDL: str = (
    "CREATE TABLE IF NOT EXISTS memories(\n"
    "  key TEXT, namespace TEXT DEFAULT 'default',\n"
    "  value TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('preference','analysis','feedback','context')),\n"
    "  embedding BLOB NOT NULL,               -- 384 float32, all-MiniLM-L6-v2\n"
    "  stability REAL NOT NULL,               -- INITIAL_STABILITY: pref 14 / feedback 10 / analysis 7 / context 3\n"
    "  score REAL DEFAULT 1.0, uses INTEGER DEFAULT 0,\n"
    "  created_at TEXT NOT NULL,              -- TRANSACTION time: when memo learned it. ISO-8601 with offset.\n"
    "  updated_at TEXT NOT NULL,              -- last system touch. ISO-8601 with offset.\n"
    "  valid_from TEXT NOT NULL,              -- VALID time start: when true of the user. May be FUTURE-dated.\n"
    "  valid_to TEXT,                         -- VALID time end, NULL = open. Set on supersession: valid_to(A) = valid_from(B).\n"
    "  status TEXT DEFAULT 'current' CHECK(status IN ('current','superseded')),\n"
    "  family_id TEXT NOT NULL,               -- stable proposition ID; all versions of one fact share it.\n"
    "  superseded_by TEXT,                    -- key of the winning row (forward link); NULL while current.\n"
    "  relation_type TEXT CHECK(relation_type IN ('supersedes','corrects','qualifies')),\n"
    "  PRIMARY KEY(key, namespace));\n"
    "CREATE INDEX IF NOT EXISTS idx_memories_family ON memories(family_id, valid_from);\n"
    "CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, namespace);\n"
    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(key, value, content='memories', content_rowid='rowid');\n"
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
    "CREATE TABLE IF NOT EXISTS archived_memories AS SELECT *, '' AS archived_at, '' AS archived_reason FROM memories LIMIT 0;\n"
    "CREATE TABLE IF NOT EXISTS episodes(id INTEGER PRIMARY KEY, summary TEXT, source_count INT, rolled_up_at TEXT,\n"
    "  started_at TEXT NOT NULL,              -- observation interval start.\n"
    "  ended_at TEXT NOT NULL);               -- observation interval end; never used as valid-time evidence."
)

SKILLS_DDL: str = (
    "CREATE TABLE IF NOT EXISTS skills(\n"
    "  name TEXT PRIMARY KEY, description TEXT NOT NULL, body TEXT NOT NULL,\n"
    "  hash TEXT NOT NULL, version INTEGER DEFAULT 1, source TEXT DEFAULT 'local',\n"
    "  created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),\n"
    "  updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));"
)


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class BaseModel(Model):
    class Meta:
        database = database


class MemoMeta(BaseModel):
    key = TextField(column_name="k", primary_key=True)
    value = TextField(column_name="v")

    class Meta:
        table_name = "memo_meta"


class Memory(BaseModel):
    key = TextField()
    namespace = TextField(default="default")
    value = TextField()
    type = TextField()
    embedding = BlobField()
    stability = FloatField()
    score = FloatField(default=1.0)
    uses = IntegerField(default=0)
    created_at = TextField()
    updated_at = TextField()
    valid_from = TextField()
    valid_to = TextField(null=True)
    status = TextField(default="current")
    family_id = TextField()
    superseded_by = TextField(null=True)
    relation_type = TextField(null=True)

    class Meta:
        table_name = "memories"
        primary_key = CompositeKey("key", "namespace")
        indexes = (
            (("family_id", "valid_from"), False),
            (("status", "namespace"), False),
        )


class MemoryFTS(FTS5Model):
    key = SearchField()
    value = SearchField()

    class Meta:
        database = database
        table_name = "memories_fts"
        options = {"content": "memories", "content_rowid": "rowid"}


class ArchivedMemory(BaseModel):
    key = TextField(null=True)
    namespace = TextField(null=True)
    value = TextField(null=True)
    type = TextField(null=True)
    embedding = BlobField(null=True)
    stability = FloatField(null=True)
    score = FloatField(null=True)
    uses = IntegerField(null=True)
    created_at = TextField(null=True)
    updated_at = TextField(null=True)
    valid_from = TextField(null=True)
    valid_to = TextField(null=True)
    status = TextField(null=True)
    family_id = TextField(null=True)
    superseded_by = TextField(null=True)
    relation_type = TextField(null=True)
    archived_at = TextField(null=True)
    archived_reason = TextField(null=True)

    class Meta:
        table_name = "archived_memories"
        primary_key = False  # CTAS clone has no PK, matches DDL


class Episode(BaseModel):
    id = AutoField(primary_key=True)
    summary = TextField(null=True)
    source_count = IntegerField(null=True)
    rolled_up_at = TextField(null=True)
    started_at = TextField()
    ended_at = TextField()

    class Meta:
        table_name = "episodes"


class Skill(BaseModel):
    name = TextField(primary_key=True)
    description = TextField()
    body = TextField()
    skill_hash = TextField(column_name="hash")
    version = IntegerField(default=1)
    source = TextField(default="local")
    created_at = TextField(default=utc_now)
    updated_at = TextField(default=utc_now)

    class Meta:
        table_name = "skills"


MODELS = [MemoMeta, Memory, MemoryFTS, ArchivedMemory, Episode, Skill]
