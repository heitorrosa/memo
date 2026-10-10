"""Skills domain: put/get/update/install + sync dual-mode + L0 index (spec §§18-21).

Import rule (§37): this module imports ONLY db/errors (never memory.py).
The only cross-domain readers are summary.py (L0 join) and maintenance.py
(tombstone expiry) — both read-only/bounded callers, never imports here.
"""

from __future__ import annotations

import datetime
import hashlib
import sqlite3

from memo import errors

FILE_HARNESSES = ("opencode", "claude-code", "filesystem")
PAGE_SIZE = 10
PAGE_BYTES = 30 * 1024
WEB_TRUNCATE_SKILLS = 150
WEB_TRUNCATE_CHARS = 32 * 1024
WEB_PAGE = 60

ORIENTATION = (
    "You hold durable memory (facts) and a skill index (workflows) below. "
    "Before any personal/project answer: memory_search. "
    "After learning anything durable: memory_save. "
    "When a skill matches the task: skill_get (never guess the body). "
    "Truncated index? Re-call skill_sync with a filter."
)


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


def live_rows(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT name, description, body, hash, version FROM skills "
        "WHERE source != 'tombstone' ORDER BY name ASC"
    ).fetchall()


def sync_files(conn: sqlite3.Connection, known: list[dict] | None,
                offset: int, limit: int | None) -> dict:
    live = live_rows(conn)
    by_name = {name: (description, body, body_hash, version) for name, description, body, body_hash, version in live}
    tombstoned = {
        r[0] for r in conn.execute(
            "SELECT name FROM skills WHERE source='tombstone'").fetchall()
    }
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
        f"{l0_line(skill[0], skill[1])} → `skill_get(\"{skill[0]}\")` for the body."
        for skill in page
    ]
    head = (f"## Skill index ({total} — full, keep in mind for the whole "
            f"conversation)" if not truncated else
            f"## Skill index ({total} — truncated, showing {len(page)} — "
            f"refine with skill_sync filter)")
    return {"mode": "bundle",
            "bootstrap_md": ORIENTATION + "\n" + head + "\n" + "\n".join(lines),
            "truncated": truncated, "total": total}


def sync(conn: sqlite3.Connection, harness: str, known: list[dict] | None = None,
         filter: str | None = None, offset: int = 0,
         limit: int | None = None) -> dict:
    """Dual-mode sync (§§20-21). File harnesses get diffs (row-ahead crash
    recovery: the row is truth, the next sync repairs files — M-022; the
    CLIENT applies deletes-THEN-writes). Web gets the L0 bundle (L1 lazy via
    get). Unknown harness -> not_found. Maintenance tail is wired by Task 7,
    not here."""
    if harness == "web":
        return sync_web(conn, filter, offset, limit)
    if harness not in FILE_HARNESSES:
        return errors.err("not_found", f"unknown harness {harness!r}")
    return sync_files(conn, known, offset, limit)


# L4 tail (Task 7, additive §23): janitor at the END of every skill_sync.
# Logic above untouched; lazy import keeps the skills→memory ban intact.
original_sync = sync


def sync(conn: sqlite3.Connection, harness: str, known: list[dict] | None = None,
         filter: str | None = None, offset: int = 0,
         limit: int | None = None) -> dict:
    from memo import maintenance

    out = original_sync(conn, harness, known=known, filter=filter,
                     offset=offset, limit=limit)
    maintenance.run(conn)
    return out


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        return None, text
    meta: dict = {}
    for line in lines[1:end]:
        if ":" in line:
            field_name, field_value = line.split(":", 1)
            meta[field_name.strip().lower()] = field_value.strip().strip("'\"")
    return meta, "\n".join(lines[end + 1:])


def default_fetch(url: str) -> str:
    import httpx  # lazy: importing memo must never load the network stack

    try:
        response = httpx.get(url, timeout=15.0, follow_redirects=True)
    except Exception as error:  # network down/timeout -> internal, no row (F-04)
        first = str(error).splitlines()[0] if str(error) else type(error).__name__
        raise RuntimeError(f"fetch failed: {first}")
    if response.status_code != 200:
        raise RuntimeError(f"fetch failed: HTTP {response.status_code} for {url}")
    return response.text


def install(conn: sqlite3.Connection, repository: str, skill_name: str,
            fetch=None) -> dict:
    """Fetch SKILL.md raw, validate frontmatter, then behave EXACTLY like put
    (plus a files_to_write delta so file harnesses materialize immediately).
    fetch(url)->str is injectable; tests MUST mock it (M-020, no live net)."""
    fetch = fetch or default_fetch
    if repository.startswith("skills.sh/"):
        slug = repository[len("skills.sh/"):]
        url = f"https://skills.sh/{slug}/skills/{skill_name}/SKILL.md"
        source = "skills.sh"
    elif repository.startswith("github:"):
        remainder = repository[len("github:"):]
        reference = "main"
        if "@" in remainder:
            remainder, reference = remainder.rsplit("@", 1) or (remainder, reference)
            if not reference:
                reference = "main"
        url = (f"https://raw.githubusercontent.com/{remainder}/{reference}/skills/"
               f"{skill_name}/SKILL.md")
        source = f"github:{remainder}"
    else:
        return errors.err("not_found", f"unknown repo {repository!r}")
    try:
        text = fetch(url)
    except Exception as error:  # noqa: BLE001 — boundary: map to envelope
        first = str(error).splitlines()[0] if str(error) else type(error).__name__
        detail = first if first.startswith("fetch failed") else f"fetch failed: {first}"
        return errors.err("internal", detail)
    meta, body = split_frontmatter(text)
    if not meta or not meta.get("name") or not meta.get("description"):
        return errors.err("not_found", f"frontmatter invalid for {skill_name!r}")
    if meta["name"] != skill_name:
        return errors.err(
            "not_found",
            f"frontmatter name mismatch: {meta['name']!r} != {skill_name!r}")
    receipt = put(conn, skill_name, meta["description"], body, source=source)
    row = get(conn, skill_name)
    receipt["files_to_write"] = [{
        "path": f"skills/{skill_name}/SKILL.md",
        "content": build_frontmatter(
            skill_name, row["description"], row["version"], row["body"]),
        "hash": receipt["hash"],
    }]
    return receipt
