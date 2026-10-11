"""Memory-domain tables: Memory + MemoryFTS + ArchivedMemory + Episode (spec §5).

Canonical per-table Model module (Peewee-canonical): these classes own the
memories-domain schema. orm.py keeps only database/bind glue; __init__
re-exports this module's names for the stable API.
"""
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
    # User-set TTL: a fact past valid_until decays out of ranking. NULL =
    # no expiry. Distinct from valid_to, which the supersede chain owns.
    valid_until = TextField(null=True)
    # Writer stamp: agent_id of the last writer (Agent model key, nullable;
    # no FK constraint so memory writes never fail on identity plumbing).
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


class ArchivedMemory(BaseMemoryModel):
    # Maps the RAW_TAIL CTAS clone (mirrors memories + 2 cols); never in
    # CREATE_MODELS — the clone owns the table shape.
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
