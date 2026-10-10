"""Peewee ORM models over the memo.models DDL (spec §5).

Deferred SqliteDatabase: db.py init()s it per path, so tests can bind
a fresh tmp file each (Peewee test pattern) with zero simultaneous-DB
fights — every fixture gets its own database object state via init().
Table/column shapes mirror models/*.py DDL exactly; CHECKs stay in DDL
(Python never re-validates them). FTS sync stays in the ai/ad/au
triggers created by the DDL; MemoryFTS only declares the external-
content mapping (content='memories', content_rowid='rowid') so
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
    hash = TextField(column_name="hash")
    version = IntegerField(default=1)
    source = TextField(default="local")
    created_at = TextField(default=utc_now)
    updated_at = TextField(default=utc_now)

    class Meta:
        table_name = "skills"


MODELS = [MemoMeta, Memory, MemoryFTS, ArchivedMemory, Episode, Skill]
