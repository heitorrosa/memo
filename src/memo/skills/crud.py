"""Skills CRUD: put/get/update/tombstone + L0 index (spec §§18-19)."""
from __future__ import annotations

import datetime
import hashlib
import sqlite3

from memo import errors


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def hash_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def shorten_text(description: str, words: int = 20) -> str:
    parts = description.split()
    return description if len(parts) <= words else " ".join(parts[:words]) + " …"


def l0_line(name: str, description: str) -> str:
    return f"- **{name}** — {shorten_text(description)}"


def build_frontmatter(name: str, description: str, version: int, body: str) -> str:
    return (
        f"---\nname: {name}\ndescription: {description}\n"
        f"version: {version}\n---\n{body}"
    )


def put(conn: sqlite3.Connection, name: str, description: str, body: str,
        source: str = "local", base_version: int | None = None) -> dict:
    """Upsert a skill. Identical body -> no-op (no version bump, #8).

    base_version pins M-019: when set and != current version, the write wins
    but the loser's body is preserved as an appended
    `## Revision vN (superseded, <date>)` section, never lost.
    Re-put of a tombstoned name resurrects it, continuing the counter (Q-04).
    Full-body rewrites go through put, NOT update (#9).
    """
    now = utc_now()
    body_hash = hash_body(body)
    row = conn.execute(
        "SELECT description, body, hash, version, source FROM skills WHERE name=?",
        (name,),
    ).fetchone()
    if row is None:
        with conn:
            conn.execute(
                "INSERT INTO skills(name,description,body,hash,version,source,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (name, description, body, body_hash, 1, source, now, now),
            )
        return {"hash": body_hash, "version": 1}
    _, old_body, old_hash, old_version, old_source = row
    if old_source == "tombstone":
        new_version = old_version + 1
        with conn:
            conn.execute(
                "UPDATE skills SET description=?,body=?,hash=?,version=?,"
                "source=?,updated_at=? WHERE name=?",
                (description, body, body_hash, new_version, source, now, name),
            )
        return {"hash": body_hash, "version": new_version}
    if body_hash == old_hash:
        return {"hash": body_hash, "version": old_version}
    if base_version is not None and base_version != old_version:
        new_body = f"{body}\n\n## Revision v{old_version} (superseded, {now})\n{old_body}"
        new_hash, new_version = hash_body(new_body), old_version + 1
        with conn:
            conn.execute(
                "UPDATE skills SET description=?,body=?,hash=?,version=?,"
                "source=?,updated_at=? WHERE name=?",
                (description, new_body, new_hash, new_version, source, now, name),
            )
        return {"hash": new_hash, "version": new_version}
    new_version = old_version + 1
    with conn:
        conn.execute(
            "UPDATE skills SET description=?,body=?,hash=?,version=?,"
            "source=?,updated_at=? WHERE name=?",
            (description, body, body_hash, new_version, source, now, name),
        )
    return {"hash": body_hash, "version": new_version}


def split_revisions(body: str) -> tuple[str, list[str]]:
    """Split an append-only body into (base, [revision sections])."""
    chunks, current = [], []
    for line in body.split("\n"):
        if line.startswith("## Revision v") and (not current or current == [""]):
            pass
        if line.startswith("## Revision v"):
            chunks.append("\n".join(current).rstrip("\n"))
            current = [line]
        else:
            current.append(line)
    if chunks:
        chunks.append("\n".join(current).rstrip("\n"))
        return chunks[0], chunks[1:]
    return body, []


def get(conn: sqlite3.Connection, name: str, version: int | None = None) -> dict:
    """Fetch a skill. version pins an old revision (rebuilt from the
    append-only base + revision sections); tombstoned/missing -> not_found."""
    row = conn.execute(
        "SELECT description, body, hash, version, source FROM skills WHERE name=?",
        (name,),
    ).fetchone()
    if row is None or row[4] == "tombstone":
        return errors.err("not_found", f"skill {name!r} not found")
    description, body, body_hash, current_version, _ = row
    if version is None or version == current_version:
        return {"name": name, "description": description, "body": body,
                "hash": body_hash, "version": current_version}
    if version < 1 or version > current_version:
        return errors.err("not_found", f"skill {name!r} has no version {version}")
    base, sections = split_revisions(body)
    # NOTE: pins are exact only within the current put generation; a later
    # full-rewrite put resets the base while the counter continues.
    old_body = base if version == 1 else (base + "\n\n" + "\n\n".join(sections[:version - 1]))
    return {"name": name, "description": description, "body": old_body,
            "hash": hash_body(old_body), "version": version}


def update(conn: sqlite3.Connection, name: str, patch: str) -> dict:
    """Append a `## Revision vN (<date>)` section; never rewrites history."""
    row = conn.execute(
        "SELECT body, version, source FROM skills WHERE name=?", (name,)
    ).fetchone()
    if row is None or row[2] == "tombstone":
        return errors.err("not_found", f"skill {name!r} not found")
    body, version, _ = row
    now, new_version = utc_now(), version + 1
    new_body = f"{body}\n\n## Revision v{new_version} ({now})\n{patch}"
    with conn:
        conn.execute(
            "UPDATE skills SET body=?,hash=?,version=?,updated_at=? WHERE name=?",
            (new_body, hash_body(new_body), new_version, now, name),
        )
    return {"hash": hash_body(new_body), "version": new_version}


def tombstone(conn: sqlite3.Connection, name: str) -> dict:
    """Soft-delete: keep the row + body 30d (maintenance drops it), surfaced
    to file harnesses via sync files_to_delete. Idempotent."""
    row = conn.execute(
        "SELECT hash, version, source FROM skills WHERE name=?", (name,)
    ).fetchone()
    if row is None:
        return errors.err("not_found", f"skill {name!r} not found")
    body_hash, version, source = row
    if source != "tombstone":
        with conn:
            conn.execute(
                "UPDATE skills SET source='tombstone',updated_at=? WHERE name=?",
                (utc_now(), name),
            )
    return {"name": name, "hash": body_hash, "version": version, "source": "tombstone"}


def l0_index(conn: sqlite3.Connection) -> list[str]:
    """L0 index lines, name asc — never insertion order (M-014)."""
    rows = conn.execute(
        "SELECT name, description FROM skills WHERE source != 'tombstone' "
        "ORDER BY name ASC"
    ).fetchall()
    return [l0_line(name, description) for name, description in rows]
