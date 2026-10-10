"""Skills-domain table: Skill (spec §5).

Canonical per-table Model module (Peewee-canonical): this class owns the
skills-domain schema. Tombstone is a ROW STATE (source='tombstone'), NOT
a table — no tombstone model exists. orm.py re-exports this module's
names for the stable memo.models.orm import path.
"""
from __future__ import annotations

import datetime

from peewee import IntegerField, Model, SQL, TextField

from memo.models.orm import database


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class Skill(Model):
    name = TextField(primary_key=True)
    description = TextField()
    body = TextField()
    skill_hash = TextField(column_name="hash")
    version = IntegerField(default=1, constraints=[SQL("DEFAULT 1")])
    source = TextField(default="local", constraints=[SQL("DEFAULT 'local'")])
    # Python default (utc_now) for ORM creates + SQL DEFAULT for raw-SQL
    # inserts: bare defaults stay Python-side only and drop the DDL clause.
    created_at = TextField(
        default=utc_now,
        constraints=[SQL("DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))")],
    )
    updated_at = TextField(
        default=utc_now,
        constraints=[SQL("DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))")],
    )

    class Meta:
        table_name = "skills"
        database = database


__all__ = ["Skill", "utc_now"]
