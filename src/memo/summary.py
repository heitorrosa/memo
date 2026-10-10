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
    for key, typ, value, uses, updated in rows:
        v = value if len(value) <= 160 else value[:160] + "…"
        lines.append(f"- [{typ}] {key}: {v} (age {age_days_label(updated)}d · used {uses}x)")
    return lines


def stats_line(conn: sqlite3.Connection) -> str:
    n = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE status='current'").fetchone()[0]
    k = conn.execute(
        "SELECT COUNT(*) FROM skills WHERE source != 'tombstone'").fetchone()[0]
    return f"Stats: {n}/250 memories · {k} skills · db v3"


def bootstrap_text(conn: sqlite3.Connection) -> str:
    mem = top_memories_lines(conn)
    l0 = skills.l0_index(conn)
    parts = [
        "# memo bootstrap — durable memory + skill index "
        "(loaded once per conversation; do not re-fetch what you hold)",
        "## Orientation",
        ORIENTATION,
        f"## Top memories ({len(mem)})",
        *(mem or ["(none yet)"]),
        f"## Skill index ({len(l0)} — full, keep in mind for the whole conversation)",
        *(l0 or ["(no skills yet)"]),
        f"## {stats_line(conn)}",
    ]
    return "\n".join(parts)
