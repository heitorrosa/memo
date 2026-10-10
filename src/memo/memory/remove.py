"""Memory soft-delete ONLY (§15, M-004): row → archived_memories, family kept."""
from __future__ import annotations

import sqlite3

from memo import temporal
from memo.memory.common import ARCHIVE_COLUMNS, fetch_row


def remove(conn: sqlite3.Connection, key: str,
           namespace: str = "default") -> dict:
    row = fetch_row(conn, key, namespace)
    if row is None:
        return {"key": key, "action": "not_found"}
    now = temporal.now_iso()
    with conn:
        conn.execute(
            f"INSERT INTO archived_memories({ARCHIVE_COLUMNS},archived_at,"
            f"archived_reason) VALUES({','.join('?' for _ in range(16))},?,?)",
            (row["key"], namespace, row["value"], row["type"], row["embedding"],
             row["stability"], row["score"], row["uses"], row["created_at"],
             row["updated_at"], row["valid_from"], row["valid_to"],
             row["status"], row["family_id"], row["superseded_by"],
             row["relation_type"], now, "deleted"))
        conn.execute("DELETE FROM memories WHERE key=? AND namespace=?",
                     (key, namespace))
    return {"key": key, "action": "deleted"}
