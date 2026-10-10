"""Bootstrap text (flag-aware)."""
from __future__ import annotations

from memo import skills, summary
from memo.models.orm import Memory as OrmMemory
from memo.models.orm import Skill as OrmSkill


def stats_line(conn, memory_on=True, skills_on=True) -> str:
    memory_count = OrmMemory.select().where(OrmMemory.status == "current").count()
    skill_count = OrmSkill.select().where(OrmSkill.source != "tombstone").count()
    memory_text = f"{memory_count}/250 memories" if memory_on else "memories DISABLED"
    skills_text = f"{skill_count} skills" if skills_on else "skills DISABLED"
    return f"Stats: {memory_text} · {skills_text} · db v3"


def bootstrap_text(conn, memory_on=True, skills_on=True) -> str:
    if memory_on and skills_on:
        return summary.bootstrap_text(conn)
    parts = ["# memo bootstrap — durable memory + skill index "
             "(loaded once per conversation; do not re-fetch what you hold)",
             "## Orientation", summary.ORIENTATION]
    if memory_on:
        memory_lines = summary.top_memories_lines(conn)
        parts += [f"## Top memories ({len(memory_lines)})",
                  *(memory_lines or ["(none yet)"])]
    if skills_on:
        skill_index = skills.l0_index(conn)
        parts += [f"## Skill index ({len(skill_index)} — full, keep in mind for the"
                  f" whole conversation)", *(skill_index or ["(no skills yet)"])]
    parts.append(f"## {stats_line(conn, memory_on, skills_on)}")
    return "\n".join(parts)
