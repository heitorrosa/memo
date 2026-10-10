"""Skills-domain table (spec §5)."""
from __future__ import annotations

SKILLS_DDL: str = (
    "CREATE TABLE IF NOT EXISTS skills(\n"
    "  name TEXT PRIMARY KEY, description TEXT NOT NULL, body TEXT NOT NULL,\n"
    "  hash TEXT NOT NULL, version INTEGER DEFAULT 1, source TEXT DEFAULT 'local',\n"
    "  created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),\n"
    "  updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));"
)
