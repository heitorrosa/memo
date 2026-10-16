from __future__ import annotations

import datetime
import json
import sqlite3
import uuid

from memo import errors
from memo import db as memo_db
from memo import maintenance
from memo import prompts as prompt_store
from memo import temporal
from memo.models import orm as orm_models
from memo.models.agents import STALE_AGENT_DAYS
from memo.models.orm import Agent, AgentState, Skill
from memo.models.skills import utc_now
from memo.skills.crud import build_frontmatter, hash_body_normalized, l0_line

PAGE_SIZE = 10
PAGE_BYTES = 30 * 1024
WEB_TRUNCATE_SKILLS = 150
WEB_TRUNCATE_CHARS = 32 * 1024
WEB_PAGE = 60

ORIENTATION = prompt_store.render("orientation")


def live_rows(conn: sqlite3.Connection) -> list[tuple]:
    with memo_db.orm_ctx(conn):
        return [(skill.name, skill.description, skill.body, skill.skill_hash, skill.version)
                for skill in Skill.select().where(Skill.source != "tombstone")
                .order_by(Skill.name.asc())]


def tombstoned(conn: sqlite3.Connection) -> set[str]:
    with memo_db.orm_ctx(conn):
        return {skill.name for skill in Skill.select(Skill.name)
                .where(Skill.source == "tombstone")}


def known_from_state(conn: sqlite3.Connection, agent_id: str) -> list[dict]:
    with memo_db.orm_ctx(conn):
        rows = list(AgentState.select(AgentState.skill, AgentState.hash)
                    .where(AgentState.agent == agent_id).tuples())
    return [{"name": name, "hash": body_hash} for name, body_hash in rows]


def skill_snapshot(conn: sqlite3.Connection) -> dict:
    with memo_db.orm_ctx(conn):
        rows = list(Skill.select(
            Skill.name, Skill.body, Skill.skill_hash, Skill.version,
            Skill.source, Skill.updated_at).tuples())
    snapshot = {}
    for name, body, stored_hash, version, source, updated_at in rows:
        snapshot[name] = {
            "hash": stored_hash if source == "tombstone"
            else hash_body_normalized(body),
            "version": version, "updated_at": updated_at}
    return snapshot


def moved(prev_last_seen: str | None, updated_at: str) -> bool:
    if prev_last_seen is None:
        return False
    try:
        return temporal.parse_ts(updated_at) > temporal.parse_ts(prev_last_seen)
    except ValueError:
        return False


def resolve_agent(conn: sqlite3.Connection, agent_id, harness: str,
                   caps, now_iso: str) -> tuple[str, str | None]:
    peewee_db = memo_db.orm_db(conn)
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        row = None
        if isinstance(agent_id, str) and agent_id:
            row = Agent.get_or_none(Agent.agent_id == agent_id)
        if row is not None:
            prev = row.last_seen
            (Agent.update(last_seen=now_iso, harness=harness,
                          capabilities=json.dumps(list(caps)))
             .where(Agent.agent_id == agent_id).execute())
            return agent_id, prev
        new_id = uuid.uuid4().hex
        Agent.create(agent_id=new_id, harness=harness,
                     capabilities=json.dumps(list(caps)),
                     first_seen=now_iso, last_seen=now_iso)
        return new_id, None


def persist_snapshot(conn: sqlite3.Connection, agent_id: str,
                      snapshot: dict, now_iso: str) -> None:
    rows = [{"agent": agent_id, "skill": name, "hash": entry["hash"],
             "version": entry["version"], "updated_at": now_iso}
            for name, entry in snapshot.items()]
    peewee_db = memo_db.orm_db(conn)
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        AgentState.delete().where(AgentState.agent == agent_id).execute()
        if rows:
            AgentState.insert_many(rows).execute()


def prune_stale_agents(conn: sqlite3.Connection, now_iso: str) -> None:
    cutoff = (temporal.parse_ts(now_iso)
              - datetime.timedelta(days=STALE_AGENT_DAYS)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    peewee_db = memo_db.orm_db(conn)
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        stale = [row.agent_id for row in
                 Agent.select(Agent.agent_id).where(Agent.last_seen < cutoff)]
        if stale:
            AgentState.delete().where(AgentState.agent.in_(stale)).execute()
            Agent.delete().where(Agent.agent_id.in_(stale)).execute()


def sync_files(conn: sqlite3.Connection, known: list[dict] | None,
                offset: int, limit: int | None) -> dict:
    live = live_rows(conn)
    by_name = {name: (description, body, body_hash, version) for name, description, body, body_hash, version in live}
    tombstoned_names = tombstoned(conn)
    all_names = set(by_name) | tombstoned_names
    if known is None:
        writes = [
            {"path": f"skills/{name}/SKILL.md",
             "content": build_frontmatter(name, description, version, body),
             "hash": hash_body_normalized(body)}
            for name, description, body, body_hash, version in live
        ]
        deletes: list[dict] = []
    else:
        have = {
            entry.get("name"): entry.get("hash") for entry in known
            if isinstance(entry.get("name"), str) and isinstance(entry.get("hash"), str)
        }
        writes = []
        for name, description, body, body_hash, version in live:
            compare_hash = hash_body_normalized(body)
            if have.get(name) != compare_hash:
                writes.append({
                    "path": f"skills/{name}/SKILL.md",
                    "content": build_frontmatter(name, description, version, body),
                    "hash": compare_hash})
        deletes = [
            {"path": f"skills/{name}/SKILL.md"}
            for name in sorted(have) if name in tombstoned_names or name not in all_names
        ]
    total = len(live)
    offset = max(0, offset)
    page, budget = [], PAGE_BYTES
    for write in writes[offset:]:
        if len(page) >= (limit if limit else PAGE_SIZE):
            break
        if page and len(write["content"].encode("utf-8")) > budget:
            break
        page.append(write)
        budget -= len(write["content"].encode("utf-8"))
    return {"mode": "files", "files_to_write": page,
            "files_to_delete": deletes, "offset": offset, "total": total}


def rank_filter(live: list[tuple], filter_text: str) -> list[tuple]:
    words = filter_text.lower().split()
    scored = []
    for row in live:
        skill_name, skill_description = row[0].lower(), row[1].lower()
        score = sum((2 if word in skill_name else 0) + (1 if word in skill_description else 0) for word in words)
        if score > 0:
            scored.append((-score, row[0], row))
    return [entry for _, _, entry in sorted(scored)]


def sync_web(conn: sqlite3.Connection, filter_text: str | None,
              offset: int, limit: int | None) -> dict:
    from memo.summary import top_memories_lines
    live = live_rows(conn)
    total = len(live)
    full_chars = sum(len(skill[0]) + len(skill[1]) for skill in live)
    truncated = total > WEB_TRUNCATE_SKILLS or full_chars > WEB_TRUNCATE_CHARS
    rows = rank_filter(live, filter_text) if filter_text else list(live)
    offset = max(0, offset)
    page = rows if not truncated else rows[offset:offset + (limit or WEB_PAGE)]
    if not truncated and (offset or limit):
        page = rows[offset:offset + limit] if limit else rows[offset:]
    lines = [
        f"{l0_line(skill[0], skill[1])}"
        f"{prompt_store.render('web_l0_suffix', name=skill[0])}"
        for skill in page
    ]
    head = (prompt_store.render("skill_index_heading_full", count=total)
            if not truncated else
            prompt_store.render("web_index_truncated_heading",
                                total=total, shown=len(page)))
    memories = top_memories_lines(conn)
    memories_section = (
        prompt_store.render("top_memories_heading", count=len(memories))
        + "\n" + "\n".join(memories or [prompt_store.render("empty_memories")]))
    return {"mode": "bundle",
            "bootstrap_md": ORIENTATION + "\n" + memories_section + "\n"
            + head + "\n" + "\n".join(lines),
            "truncated": truncated, "total": total}


def sync(conn: sqlite3.Connection, harness: str, known: list[dict] | None = None,
         filter: str | None = None, offset: int = 0,
         limit: int | None = None,
         capabilities: list[str] | None = None,
         agent_id: str | None = None) -> dict:
    if not isinstance(harness, str) or not harness:
        return errors.err("not_found", prompt_store.render(
            "err_unknown_harness", harness=harness))
    caps = capabilities if isinstance(capabilities, (list, tuple, set)) else ()
    now_iso = utc_now()
    prune_stale_agents(conn, now_iso)
    resolved_id, prev_last_seen = resolve_agent(conn, agent_id, harness, caps,
                                                now_iso)
    if known is None and prev_last_seen is not None:
        known = known_from_state(conn, resolved_id)
    if caps:
        out = sync_files(conn, known, offset, limit)
    else:
        out = sync_web(conn, filter, offset, limit)
    snapshot = skill_snapshot(conn)
    if prev_last_seen is not None:
        moved_names = sorted(name for name, entry in snapshot.items()
                             if moved(prev_last_seen, entry["updated_at"]))
        for entry in out.get("files_to_write", []) + out.get("files_to_delete", []):
            if entry["path"].split("/")[1] in moved_names:
                entry["moved_since_last_sync"] = True
        out["moved_since_last_sync"] = moved_names
    out["agent_id"] = resolved_id
    persist_snapshot(conn, resolved_id, snapshot, now_iso)
    return out


original_sync = sync


def sync(conn: sqlite3.Connection, harness: str, known: list[dict] | None = None,
         filter: str | None = None, offset: int = 0,
         limit: int | None = None,
         capabilities: list[str] | None = None,
         agent_id: str | None = None) -> dict:
    out = original_sync(conn, harness, known=known, filter=filter,
                     offset=offset, limit=limit, capabilities=capabilities,
                     agent_id=agent_id)
    maintenance.run(conn)
    return out
