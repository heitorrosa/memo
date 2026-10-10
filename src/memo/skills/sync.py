"""Skills sync dual-mode + L0 bundle (spec §§20-21) + L4 janitor tail (§23)."""
from __future__ import annotations

import sqlite3

from memo import errors
from memo import db as memo_db
from memo import maintenance
from memo import prompts as prompt_store
from memo.models.orm import Skill
from memo.skills.crud import build_frontmatter, l0_line

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


def sync_files(conn: sqlite3.Connection, known: list[dict] | None,
                offset: int, limit: int | None) -> dict:
    live = live_rows(conn)
    by_name = {name: (description, body, body_hash, version) for name, description, body, body_hash, version in live}
    tombstoned = _tombstoned(conn)
    all_names = set(by_name) | tombstoned
    if known is None:
        writes = [
            {"path": f"skills/{name}/SKILL.md",
             "content": build_frontmatter(name, description, version, body), "hash": body_hash}
            for name, description, body, body_hash, version in live
        ]
        deletes: list[dict] = []
    else:
        have = {
            entry.get("name"): entry.get("hash") for entry in known
            if isinstance(entry.get("name"), str) and isinstance(entry.get("hash"), str)
        }
        writes = [
            {"path": f"skills/{name}/SKILL.md",
             "content": build_frontmatter(name, description, version, body), "hash": body_hash}
            for name, description, body, body_hash, version in live if have.get(name) != body_hash
        ]
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
         capabilities: list[str] | None = None) -> dict:
    """Dual-mode sync (§§20-21). Route by the agent's DECLARED persist verdict,
    never by name (§§20-21): a non-empty capabilities declaration means the
    caller can persist and yields a file diff; every other caller (undeclared,
    bare legacy names, unknown) gets the L0 bundle, never an error. Only a
    malformed harness (non-string or empty) is not_found. File-delta receivers
    apply deletes-THEN-writes; row-ahead crash recovery keeps the row as truth
    (M-022). Maintenance tail is wired by Task 7, not here."""
    if not isinstance(harness, str) or not harness:
        return errors.err("not_found", prompt_store.render(
            "err_unknown_harness", harness=harness))
    caps = capabilities if isinstance(capabilities, (list, tuple, set)) else ()
    if caps:  # any declared persist verdict -> files delta; names never matched
        return sync_files(conn, known, offset, limit)
    return sync_web(conn, filter, offset, limit)


# L4 tail (Task 7, additive §23): janitor at the END of every skill_sync.
# Logic above untouched (top-level import: maintenance never imports back).
original_sync = sync


def sync(conn: sqlite3.Connection, harness: str, known: list[dict] | None = None,  # noqa: F811 — tail wraps the sync above
         filter: str | None = None, offset: int = 0,
         limit: int | None = None,
         capabilities: list[str] | None = None) -> dict:
    out = original_sync(conn, harness, known=known, filter=filter,
                     offset=offset, limit=limit, capabilities=capabilities)
    maintenance.run(conn)
    return out
