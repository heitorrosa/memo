"""Skills sync dual-mode + L0 bundle (spec §§20-21) + L4 janitor tail (§23)."""
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

# Routing is by the agent's DECLARED persist verdict, never by name: any
# non-empty capabilities declaration yields a file delta; every other caller
# (undeclared, bare legacy names, unknown) gets the L0 bundle. No harness or
# tool name is ever consulted.
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


def _tombstoned(conn: sqlite3.Connection) -> set[str]:
    with memo_db.orm_ctx(conn):
        return {skill.name for skill in Skill.select(Skill.name)
                .where(Skill.source == "tombstone")}


def _known_from_state(conn: sqlite3.Connection, agent_id: str) -> list[dict]:
    """Stored per-skill snapshot -> known[] for id-only callers (no known[])."""
    with memo_db.orm_ctx(conn):
        rows = list(AgentState.select(AgentState.skill, AgentState.hash)
                    .where(AgentState.agent == agent_id).tuples())
    return [{"name": name, "hash": body_hash} for name, body_hash in rows]


def _skill_snapshot(conn: sqlite3.Connection) -> dict:
    """Every skill row -> {name: {hash, version, updated_at}}.

    Live rows store the NORMALIZED body hash — the value sync echoes — so a
    snapshot-filled known[] converges to an empty delta; tombstones keep the
    stored hash (name presence alone drives the delete, F-12).
    """
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


def _moved(prev_last_seen: str | None, updated_at: str) -> bool:
    """Server-computed nudge: skill touched after the agent's previous sync."""
    if prev_last_seen is None:
        return False
    try:
        return temporal.parse_ts(updated_at) > temporal.parse_ts(prev_last_seen)
    except ValueError:
        return False


def _resolve_agent(conn: sqlite3.Connection, agent_id, harness: str,
                   caps, now_iso: str) -> tuple[str, str | None]:
    """(agent_id, prev_last_seen): touch a known id, mint on absent/unknown.

    prev_last_seen is the value BEFORE this call (None for a fresh mint) —
    the baseline the moved-since-last-sync flags compare against.
    """
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


def _persist_snapshot(conn: sqlite3.Connection, agent_id: str,
                      snapshot: dict, now_iso: str) -> None:
    """Replace the agent's stored per-skill snapshot (hash+version)."""
    rows = [{"agent": agent_id, "skill": name, "hash": entry["hash"],
             "version": entry["version"], "updated_at": now_iso}
            for name, entry in snapshot.items()]
    peewee_db = memo_db.orm_db(conn)
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        AgentState.delete().where(AgentState.agent == agent_id).execute()
        if rows:
            AgentState.insert_many(rows).execute()


def _prune_stale_agents(conn: sqlite3.Connection, now_iso: str) -> None:
    """Lazy staleness sweep on any sync: drop agents (and their state) whose
    last_seen is older than STALE_AGENT_DAYS."""
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
    tombstoned = _tombstoned(conn)
    all_names = set(by_name) | tombstoned
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
            # Normalized comparison: whitespace-only drift never looks dirty.
            compare_hash = hash_body_normalized(body)
            if have.get(name) != compare_hash:
                writes.append({
                    "path": f"skills/{name}/SKILL.md",
                    "content": build_frontmatter(name, description, version, body),
                    "hash": compare_hash})
        # Tombstoned OR unknown-to-server names the client lists -> delete.
        # A client that applied the delete stops listing the name, so each
        # delete surfaces once; a stale known re-gets it (F-12), never the body.
        deletes = [
            {"path": f"skills/{name}/SKILL.md"}
            for name in sorted(have) if name in tombstoned or name not in all_names
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
    return {"mode": "bundle",
            "bootstrap_md": ORIENTATION + "\n" + head + "\n" + "\n".join(lines),
            "truncated": truncated, "total": total}


def sync(conn: sqlite3.Connection, harness: str, known: list[dict] | None = None,
         filter: str | None = None, offset: int = 0,
         limit: int | None = None,
         capabilities: list[str] | None = None,
         agent_id: str | None = None) -> dict:
    """Dual-mode sync (§§20-21). Route by the agent's DECLARED persist verdict,
    never by name (§§20-21): a non-empty capabilities declaration means the
    caller can persist and yields a file diff; every other caller (undeclared,
    bare legacy names, unknown) gets the L0 bundle, never an error. Only a
    malformed harness (non-string or empty) is not_found. File-delta receivers
    apply deletes-THEN-writes; row-ahead crash recovery keeps the row as truth
    (M-022). Maintenance tail is wired by Task 7, not here.

    Server-side identity: an absent/unknown agent_id mints a fresh uuid4hex and
    returns it as `agent_id` (both modes); a known id is touched and its
    post-sync per-skill snapshot persisted. An id-only caller may omit known[]
    (the server fills it from the stored snapshot). Skills touched after the
    agent's previous last_seen are flagged `moved_since_last_sync`.
    """
    if not isinstance(harness, str) or not harness:
        return errors.err("not_found", prompt_store.render(
            "err_unknown_harness", harness=harness))
    caps = capabilities if isinstance(capabilities, (list, tuple, set)) else ()
    now_iso = utc_now()
    _prune_stale_agents(conn, now_iso)
    resolved_id, prev_last_seen = _resolve_agent(conn, agent_id, harness, caps,
                                                 now_iso)
    if known is None and prev_last_seen is not None:
        known = _known_from_state(conn, resolved_id)
    if caps:  # any declared persist verdict -> files delta; names never matched
        out = sync_files(conn, known, offset, limit)
    else:
        out = sync_web(conn, filter, offset, limit)
    snapshot = _skill_snapshot(conn)
    if prev_last_seen is not None:
        moved = sorted(name for name, entry in snapshot.items()
                       if _moved(prev_last_seen, entry["updated_at"]))
        for entry in out.get("files_to_write", []) + out.get("files_to_delete", []):
            if entry["path"].split("/")[1] in moved:
                entry["moved_since_last_sync"] = True
        out["moved_since_last_sync"] = moved
    out["agent_id"] = resolved_id
    _persist_snapshot(conn, resolved_id, snapshot, now_iso)
    return out


# L4 tail (Task 7, additive §23): janitor at the END of every skill_sync.
# Logic above untouched (top-level import: maintenance never imports back).
original_sync = sync


def sync(conn: sqlite3.Connection, harness: str, known: list[dict] | None = None,  # noqa: F811 — tail wraps the sync above
         filter: str | None = None, offset: int = 0,
         limit: int | None = None,
         capabilities: list[str] | None = None,
         agent_id: str | None = None) -> dict:
    out = original_sync(conn, harness, known=known, filter=filter,
                     offset=offset, limit=limit, capabilities=capabilities,
                     agent_id=agent_id)
    maintenance.run(conn)
    return out
