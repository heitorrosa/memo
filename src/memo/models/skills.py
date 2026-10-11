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
