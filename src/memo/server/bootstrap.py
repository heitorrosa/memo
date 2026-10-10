"""Bootstrap text (flag-aware)."""
from __future__ import annotations

from memo import skills, summary
from memo import db as memo_db
from memo import prompts as prompt_store
from memo.models.orm import Memory as OrmMemory
from memo.models.orm import Skill as OrmSkill


def stats_line(conn, memory_on=True, skills_on=True) -> str:
    with memo_db.orm_ctx(conn):
        memory_count = OrmMemory.select().where(OrmMemory.status == "current").count()
        skill_count = OrmSkill.select().where(OrmSkill.source != "tombstone").count()
    memory_text = f"{memory_count}/250 memories" if memory_on else "memories DISABLED"
    skills_text = f"{skill_count} skills" if skills_on else "skills DISABLED"
    return f"Stats: {memory_text} · {skills_text} · db v3"


def bootstrap_text(conn, memory_on=True, skills_on=True) -> str:
    if memory_on and skills_on:
        return summary.bootstrap_text(conn)
    parts = [prompt_store.render("bootstrap_title"),
             prompt_store.render("heading_orientation"), skills.ORIENTATION]
    if memory_on:
        memory_lines = summary.top_memories_lines(conn)
        parts += [prompt_store.render("top_memories_heading",
                                      count=len(memory_lines)),
                  *(memory_lines or [prompt_store.render("empty_memories")])]
    if skills_on:
        skill_index = skills.l0_index(conn)
        parts += [prompt_store.render("skill_index_heading_full",
                                      count=len(skill_index)),
                  *(skill_index or [prompt_store.render("empty_skills")])]
    parts.append(f"## {stats_line(conn, memory_on, skills_on)}")
    return "\n".join(parts)
