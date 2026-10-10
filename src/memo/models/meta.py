"""memo_meta registry table (spec §5)."""
from __future__ import annotations

META_DDL: str = (
    "CREATE TABLE IF NOT EXISTS memo_meta(\n"
    "  k TEXT PRIMARY KEY, v TEXT NOT NULL);"
)
