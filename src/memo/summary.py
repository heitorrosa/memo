"""Bootstrap summary: orientation + top memories + L0 skill index (spec §22).

L3 filled the {skills_l0} join; the memory half (top_memories_lines ranking)
is refined by the memory lane (Task 4) — current shape is updated_at-ordered
and embed-free so this module needs no memory.py import.
"""

from __future__ import annotations

import datetime
import sqlite3

from memo import skills

ORIENTATION = skills.ORIENTATION


def age_days_label(updated_at: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(updated_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return str(max(0, (datetime.datetime.now(datetime.timezone.utc) - dt).days))
    except ValueError:
        return "?"


def top_memories_lines(conn: sqlite3.Connection, limit: int = 10) -> list[str]:
    rows = conn.execute(
        "SELECT key, type, value, uses, updated_at FROM memories "
        "WHERE status='current' ORDER BY updated_at DESC LIMIT ?", (limit,)
    ).fetchall()
    lines = []
    for key, memory_type, value, uses, updated_at in rows:
        shown = value if len(value) <= 160 else value[:160] + "…"
        lines.append(f"- [{memory_type}] {key}: {shown} (age {age_days_label(updated_at)}d · used {uses}x)")
    return lines


def stats_line(conn: sqlite3.Connection) -> str:
    memory_count = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE status='current'").fetchone()[0]
    skill_count = conn.execute(
        "SELECT COUNT(*) FROM skills WHERE source != 'tombstone'").fetchone()[0]
    return f"Stats: {memory_count}/250 memories · {skill_count} skills · db v3"


def bootstrap_text(conn: sqlite3.Connection) -> str:
    top_memories = top_memories_lines(conn)
    skill_index = skills.l0_index(conn)
    parts = [
        "# memo bootstrap — durable memory + skill index "
        "(loaded once per conversation; do not re-fetch what you hold)",
        "## Orientation",
        ORIENTATION,
        f"## Top memories ({len(top_memories)})",
        *(top_memories or ["(none yet)"]),
        f"## Skill index ({len(skill_index)} — full, keep in mind for the whole conversation)",
        *(skill_index or ["(no skills yet)"]),
        f"## {stats_line(conn)}",
    ]
    return "\n".join(parts)
