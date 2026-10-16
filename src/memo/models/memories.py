from __future__ import annotations

from peewee import (
    AutoField,
    BlobField,
    Check,
    CompositeKey,
    FloatField,
    IntegerField,
    Model,
    SQL,
    TextField,
)
from playhouse.sqlite_ext import FTS5Model, SearchField

from memo.models.orm import database


class BaseMemoryModel(Model):
    class Meta:
        database = database


class Memory(BaseMemoryModel):
    key = TextField()
    namespace = TextField(
        default="default", constraints=[SQL("DEFAULT 'default'")]
    )
    value = TextField()
    type = TextField()
    embedding = BlobField()
    stability = FloatField()
    score = FloatField(default=1.0, constraints=[SQL("DEFAULT 1.0")])
    uses = IntegerField(default=0, constraints=[SQL("DEFAULT 0")])
    created_at = TextField()
    updated_at = TextField()
    valid_from = TextField()
    valid_to = TextField(null=True)
    valid_until = TextField(null=True)
    agent_id = TextField(null=True)
    status = TextField(
        default="current", constraints=[SQL("DEFAULT 'current'")]
    )
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
        constraints = [
            Check("type IN ('preference','analysis','feedback','context')"),
            Check("status IN ('current','superseded')"),
            Check("relation_type IN ('supersedes','corrects','qualifies')"),
        ]


class MemoryFTS(FTS5Model):
    key = SearchField()
    value = SearchField()

    class Meta:
        database = database
        table_name = "memories_fts"
        options = {"content": "memories", "content_rowid": "rowid"}


class Edge(BaseMemoryModel):
    src_key = TextField()
    dst_key = TextField()
    rel = TextField()
    weight = FloatField(default=1.0, constraints=[SQL("DEFAULT 1.0")])
    created_at = TextField()

    class Meta:
        table_name = "edges"
        primary_key = CompositeKey("src_key", "dst_key", "rel")
        constraints = [
            Check("rel IN ('mentions','supports','contradicts','derived_from')"),
        ]


class Alias(BaseMemoryModel):
    segment = TextField()
    canonical = TextField()
    declarers = TextField(default="", constraints=[SQL("DEFAULT ''")])
    status = TextField(
        default="candidate", constraints=[SQL("DEFAULT 'candidate'")]
    )
    evidence = IntegerField(default=1, constraints=[SQL("DEFAULT 1")])
    first_seen = TextField()
    last_seen = TextField()

    class Meta:
        table_name = "aliases"
        primary_key = CompositeKey("segment", "canonical")
        constraints = [
            Check("status IN ('candidate','confirmed')"),
        ]


class EdgeDeclarer(BaseMemoryModel):
    src_key = TextField()
    dst_key = TextField()
    rel = TextField()
    declarers = TextField(default="", constraints=[SQL("DEFAULT ''")])
    status = TextField(
        default="candidate", constraints=[SQL("DEFAULT 'candidate'")]
    )
    evidence = IntegerField(default=1, constraints=[SQL("DEFAULT 1")])
    first_seen = TextField()
    last_seen = TextField()

    class Meta:
        table_name = "edge_declarers"
        primary_key = CompositeKey("src_key", "dst_key", "rel")
        constraints = [
            Check("status IN ('candidate','confirmed')"),
        ]


class ArchivedMemory(BaseMemoryModel):
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
    valid_until = TextField(null=True)
    agent_id = TextField(null=True)
    status = TextField(null=True)
    family_id = TextField(null=True)
    superseded_by = TextField(null=True)
    relation_type = TextField(null=True)
    archived_at = TextField(null=True)
    archived_reason = TextField(null=True)

    class Meta:
        table_name = "archived_memories"
        primary_key = False


class Episode(BaseMemoryModel):
    id = AutoField(primary_key=True)
    summary = TextField(null=True)
    source_count = IntegerField(null=True)
    rolled_up_at = TextField(null=True)
    started_at = TextField()
    ended_at = TextField()

    class Meta:
        table_name = "episodes"
