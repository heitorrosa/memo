from __future__ import annotations

from peewee import (
    CompositeKey,
    ForeignKeyField,
    IntegerField,
    Model,
    SQL,
    TextField,
)

from memo.models.orm import database
from memo.models.skills import utc_now

STALE_AGENT_DAYS = 90.0


class Agent(Model):
    agent_id = TextField(primary_key=True)
    harness = TextField()
    capabilities = TextField(default="[]", constraints=[SQL("DEFAULT '[]'")])
    first_seen = TextField(
        default=utc_now,
        constraints=[SQL("DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))")],
    )
    last_seen = TextField(
        default=utc_now,
        constraints=[SQL("DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))")],
    )

    class Meta:
        table_name = "agents"
        database = database


class AgentState(Model):
    agent = ForeignKeyField(Agent, on_delete="CASCADE")
    skill = TextField()
    hash = TextField()
    version = IntegerField(default=1, constraints=[SQL("DEFAULT 1")])
    updated_at = TextField(
        default=utc_now,
        constraints=[SQL("DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))")],
    )

    class Meta:
        table_name = "agent_state"
        database = database
        primary_key = CompositeKey("agent", "skill")
