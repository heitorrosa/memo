"""Bootstrap summary: orientation + top memories + L0 skill index (spec §22).

L3 filled the {skills_l0} join; the memory half (top_memories_lines ranking)
is refined by the memory lane (Task 4) — current shape is updated_at-ordered
and embed-free so this module needs no memory.py import.
"""

from __future__ import annotations

import datetime
import sqlite3

from memo import skills
from memo import db as memo_db
from memo import prompts as prompt_store
from memo.models.orm import Memory as OrmMemory
from memo.models.orm import Skill as OrmSkill

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
    with memo_db.orm_ctx(conn):
        rows = list((OrmMemory.select().where(OrmMemory.status == "current")
                     .order_by(OrmMemory.updated_at.desc()).limit(limit)))
    lines = []
    for row in rows:
        shown = row.value if len(row.value) <= 160 else row.value[:160] + "…"
        lines.append(prompt_store.render("memory_line", type=row.type,
                                         key=row.key, value=shown,
                                         age_days=age_days_label(row.updated_at),
                                         uses=row.uses))
    return lines


def stats_line(conn: sqlite3.Connection) -> str:
    with memo_db.orm_ctx(conn):
        memory_count = OrmMemory.select().where(OrmMemory.status == "current").count()
        skill_count = OrmSkill.select().where(OrmSkill.source != "tombstone").count()
    return prompt_store.render("stats_line", memory_count=memory_count,
                               skill_count=skill_count)


def bootstrap_text(conn: sqlite3.Connection) -> str:
    top_memories = top_memories_lines(conn)
    skill_index = skills.l0_index(conn)
    parts = [
        prompt_store.render("bootstrap_title"),
        prompt_store.render("heading_orientation"),
        ORIENTATION,
        prompt_store.render("top_memories_heading", count=len(top_memories)),
        *(top_memories or [prompt_store.render("empty_memories")]),
        prompt_store.render("skill_index_heading_full", count=len(skill_index)),
        *(skill_index or [prompt_store.render("empty_skills")]),
        f"## {stats_line(conn)}",
    ]
    return "\n".join(parts)
