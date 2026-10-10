"""Agents-domain tables: Agent + AgentState (server-side sync identity).

Canonical per-table Model module (Peewee-canonical): these classes own the
agents-domain schema. The server tracks each connected agent's identity and
its last-synced per-skill snapshot so a harness can carry only an id instead
of a full known[] hash list. orm.py re-exports these names for the stable
memo.models.orm import path.
"""
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

# Lazy, config-free staleness window: any sync prunes agents untouched longer
# than this. Deliberately a constant — no knob to tune.
STALE_AGENT_DAYS = 90.0


class Agent(Model):
    agent_id = TextField(primary_key=True)
    harness = TextField()
    # JSON list of the agent's declared capability labels (opaque; presence
    # alone routes, the values are never matched).
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
    # FK column defaults to "agent_id"; CASCADE drops state with its agent.
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
