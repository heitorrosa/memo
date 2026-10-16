from __future__ import annotations

import sqlite3

from memo import db as memo_db
from memo import temporal
from memo.models.orm import Edge as OrmEdge
from memo.models.orm import Memory as OrmMemory

MAX_DEPTH = 2


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
