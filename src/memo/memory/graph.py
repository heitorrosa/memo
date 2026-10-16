from __future__ import annotations

import sqlite3

from memo import db as memo_db
from memo import temporal
from memo.models.orm import Alias as OrmAlias
from memo.models.orm import Edge as OrmEdge
from memo.models.orm import EdgeDeclarer as OrmEdgeDeclarer
from memo.models.orm import Memory as OrmMemory

MAX_DEPTH = 2

# Agent-declared link relations. `same_as` resolves into an alias row and is
# never stored as an edge; the ladder (supersedes/corrects/qualifies) owns all
# versioning, so no rel here may claim supersession.
RELS = ("mentions", "supports", "contradicts", "derived_from", "same_as")
VERSIONING_RELS = frozenset({
    "supersedes", "supersede", "superseded_by", "corrects", "qualifies",
    "replaces", "version_of", "version", "reasserts",
})
MAX_LINKS = 10
MAX_ALIASES = 5

# Agreed edge weight = base(rel) x agreement multiplier, recomputed on every
# new declaration. contradicts is flag-only (0.0), never scored.
BASE_WEIGHT = {"derived_from": 1.0, "mentions": 0.5, "supports": 0.8,
               "contradicts": 0.0}
MULTIPLIER = {"candidate": 0.5, "confirmed": 1.0}


def valid_node(conn: sqlite3.Connection, key: str, at) -> bool:
    """Temporal-validity gate for a graph node at `at`.

    A node with no memories row is a VIRTUAL entity (a dotted key's leading
    segment) and is always valid. A node backed by rows is valid when any row
    is effective (not superseded, valid_from reached) and not expired.
    """
    with memo_db.orm_ctx(conn):
        rows = list(
            OrmMemory.select(
                OrmMemory.status,
                OrmMemory.valid_from,
                OrmMemory.valid_to,
                OrmMemory.valid_until,
            )
            .where(OrmMemory.key == key)
            .tuples()
        )
    if not rows:
        return True
    for status, valid_from, valid_to, valid_until in rows:
        row = {
            "status": status,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "valid_until": valid_until,
        }
        if temporal.is_effective(row, at) and not temporal.is_expired(row, at):
            return True
    return False


def neighbors(conn: sqlite3.Connection, node: str) -> list[tuple[str, str, float]]:
    """Edges touching `node` in either direction: (other_key, rel, weight)."""
    with memo_db.orm_ctx(conn):
        rows = list(
            OrmEdge.select(
                OrmEdge.src_key, OrmEdge.dst_key, OrmEdge.rel, OrmEdge.weight
            )
            .where((OrmEdge.src_key == node) | (OrmEdge.dst_key == node))
            .order_by(OrmEdge.src_key, OrmEdge.dst_key, OrmEdge.rel)
            .tuples()
        )
    return [
        (dst, rel, weight) if src == node else (src, rel, weight)
        for src, dst, rel, weight in rows
    ]


def neighborhood(conn: sqlite3.Connection, key: str, depth: int = 1) -> list[dict]:
    """Read-only BFS over the edges graph, both directions, 1-2 hops.

    Pure read: no writes, no ranking changes. Every returned node is filtered
    by temporal validity at now, so superseded and past-valid_until rows (and
    anything reachable only through them) are dropped. Order is deterministic:
    hops asc, weight desc, key asc.
    """
    if depth < 1:
        return []
    depth = min(depth, MAX_DEPTH)
    at = temporal.parse_ts(temporal.now_iso())
    found: dict[str, dict] = {}
    visited = {key}
    frontier = [key]
    for hop in range(1, depth + 1):
        next_frontier = []
        for node in frontier:
            for other, rel, weight in neighbors(conn, node):
                if other in visited:
                    continue
                visited.add(other)
                if not valid_node(conn, other, at):
                    continue
                found[other] = {
                    "key": other,
                    "rel": rel,
                    "hops": hop,
                    "weight": weight,
                }
                next_frontier.append(other)
        frontier = next_frontier
    return sorted(
        found.values(), key=lambda row: (row["hops"], -row["weight"], row["key"])
    )


def key_exists(conn: sqlite3.Connection, key: str) -> bool:
    """A link target exists as a memory key OR as a virtual entity node.

    Virtual entities have no row of their own; a dotted key's leading segment
    (for example `homelab` for `homelab.memory.x`) counts as existing.
    """
    with memo_db.orm_ctx(conn):
        if OrmMemory.select().where(OrmMemory.key == key).exists():
            return True
        return OrmMemory.select().where(
            OrmMemory.key.startswith(key + ".")).exists()


def validate_links(conn: sqlite3.Connection, key: str,
                   links) -> tuple[list[tuple[str, str]], list[str]]:
    """Deterministic, cheap link validation. Returns (accepted, notes).

    Rejections never block the save; they come back as suggestion notes. A
    duplicate (to, rel) is idempotent (kept once). Over-cap entries are dropped.
    """
    accepted: list[tuple[str, str]] = []
    notes: list[str] = []
    if links is None:
        return accepted, notes
    if not isinstance(links, list):
        return accepted, ["links: expected a list of {to, rel} objects"]
    seen: set[tuple[str, str]] = set()
    for item in links:
        if not isinstance(item, dict):
            notes.append("links: entry must be an object with to and rel")
            continue
        to, rel = item.get("to"), item.get("rel")
        if not isinstance(to, str) or not to:
            notes.append("links: missing target key (to)")
            continue
        if not isinstance(rel, str) or not rel:
            notes.append(f"links: missing rel for {to!r}")
            continue
        rel = rel.lower()
        if rel not in RELS:
            if rel in VERSIONING_RELS:
                notes.append(
                    f"links: {rel!r} claims versioning, the save ladder owns"
                    " supersession")
            else:
                notes.append(f"links: bad rel {rel!r}")
            continue
        if to == key:
            notes.append(f"links: self-link {to!r} rejected")
            continue
        if rel != "same_as" and not key_exists(conn, to):
            notes.append(f"links: target {to!r} does not exist")
            continue
        if (to, rel) in seen:
            continue
        seen.add((to, rel))
        if len(accepted) >= MAX_LINKS:
            continue
        accepted.append((to, rel))
    if len(seen) > MAX_LINKS:
        notes.append(
            f"links: {len(seen) - MAX_LINKS} over cap {MAX_LINKS} dropped")
    return accepted, notes


def validate_aliases(aliases) -> tuple[list[tuple[str, str]], list[str]]:
    """Alias validation mirroring validate_links: (segment, canonical) pairs."""
    accepted: list[tuple[str, str]] = []
    notes: list[str] = []
    if aliases is None:
        return accepted, notes
    if not isinstance(aliases, list):
        return accepted, ["aliases: expected a list of {segment, canonical} objects"]
    seen: set[tuple[str, str]] = set()
    for item in aliases:
        if not isinstance(item, dict):
            notes.append("aliases: entry must be an object with segment and canonical")
            continue
        segment, canonical = item.get("segment"), item.get("canonical")
        if not isinstance(segment, str) or not segment:
            notes.append("aliases: missing segment")
            continue
        if not isinstance(canonical, str) or not canonical:
            notes.append(f"aliases: missing canonical for {segment!r}")
            continue
        if segment == canonical:
            notes.append(f"aliases: self-alias {segment!r} rejected")
            continue
        if (segment, canonical) in seen:
            continue
        seen.add((segment, canonical))
        if len(accepted) >= MAX_ALIASES:
            continue
        accepted.append((segment, canonical))
    if len(seen) > MAX_ALIASES:
        notes.append(
            f"aliases: {len(seen) - MAX_ALIASES} over cap {MAX_ALIASES} dropped")
    return accepted, notes


def record_alias(conn: sqlite3.Connection, segment: str, canonical: str,
                 declarer: str) -> str:
    """Upsert one alias declaration; returns the resulting status.

    First declaration -> candidate, evidence 1. Same declarer again ->
    evidence+1, still candidate (an app cannot confirm itself). A second
    DISTINCT declarer -> confirmed. Competing canonicals for one segment are
    separate rows and both persist.
    """
    now = temporal.now_iso()
    db = memo_db.orm_db(conn)
    with memo_db.orm_ctx(conn):
        row = OrmAlias.get_or_none(
            (OrmAlias.segment == segment) & (OrmAlias.canonical == canonical))
    if row is None:
        db.execute_sql(
            "INSERT INTO aliases(segment,canonical,declarers,status,evidence,"
            "first_seen,last_seen) VALUES(?,?,?,?,?,?,?)",
            (segment, canonical, declarer, "candidate", 1, now, now))
        return "candidate"
    declarers = [name for name in (row.declarers or "").split(",") if name]
    if declarer in declarers:
        db.execute_sql(
            "UPDATE aliases SET evidence=evidence+1, last_seen=?"
            " WHERE segment=? AND canonical=?", (now, segment, canonical))
        return row.status
    declarers.append(declarer)
    db.execute_sql(
        "UPDATE aliases SET declarers=?, status='confirmed',"
        " evidence=evidence+1, last_seen=? WHERE segment=? AND canonical=?",
        (",".join(declarers), now, segment, canonical))
    return "confirmed"


def record_link(conn: sqlite3.Connection, src: str, dst: str, rel: str,
                declarer: str) -> None:
    """Upsert one agent-declared edge with its agreed weight.

    A server-derived edge (one with no declarer row) is a duplicate and is left
    untouched, so auto-emission keeps its weight. Otherwise the declarer row is
    upserted and the edge weight recomputed: base(rel) x multiplier(status).
    """
    now = temporal.now_iso()
    db = memo_db.orm_db(conn)
    with memo_db.orm_ctx(conn):
        edge = OrmEdge.get_or_none(
            (OrmEdge.src_key == src) & (OrmEdge.dst_key == dst)
            & (OrmEdge.rel == rel))
        declared = OrmEdgeDeclarer.get_or_none(
            (OrmEdgeDeclarer.src_key == src) & (OrmEdgeDeclarer.dst_key == dst)
            & (OrmEdgeDeclarer.rel == rel))
    if edge is not None and declared is None:
        return
    if declared is None:
        db.execute_sql(
            "INSERT INTO edge_declarers(src_key,dst_key,rel,declarers,status,"
            "evidence,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?)",
            (src, dst, rel, declarer, "candidate", 1, now, now))
        status = "candidate"
    else:
        declarers = [name for name in (declared.declarers or "").split(",") if name]
        if declarer in declarers:
            db.execute_sql(
                "UPDATE edge_declarers SET evidence=evidence+1, last_seen=?"
                " WHERE src_key=? AND dst_key=? AND rel=?",
                (now, src, dst, rel))
            status = declared.status
        else:
            declarers.append(declarer)
            status = "confirmed"
            db.execute_sql(
                "UPDATE edge_declarers SET declarers=?, status='confirmed',"
                " evidence=evidence+1, last_seen=?"
                " WHERE src_key=? AND dst_key=? AND rel=?",
                (",".join(declarers), now, src, dst, rel))
    weight = BASE_WEIGHT[rel] * MULTIPLIER[status]
    db.execute_sql(
        "INSERT OR IGNORE INTO edges(src_key,dst_key,rel,weight,created_at)"
        " VALUES(?,?,?,?,?)", (src, dst, rel, weight, now))
    db.execute_sql(
        "UPDATE edges SET weight=? WHERE src_key=? AND dst_key=? AND rel=?",
        (weight, src, dst, rel))


def record_declarations(conn: sqlite3.Connection, key: str, links, aliases,
                        declarer: str) -> str | None:
    """Apply a save's links/aliases declarations; returns suggestion notes.

    Additive and non-blocking: the fact has already landed. `same_as` links
    resolve into alias rows instead of edges.
    """
    accepted_links, notes = validate_links(conn, key, links)
    accepted_aliases, alias_notes = validate_aliases(aliases)
    notes.extend(alias_notes)
    for to, rel in accepted_links:
        if rel == "same_as":
            record_alias(conn, key, to, declarer)
        else:
            record_link(conn, key, to, rel, declarer)
    for segment, canonical in accepted_aliases:
        record_alias(conn, segment, canonical, declarer)
    return "; ".join(notes) if notes else None
