"""Memory soft-delete ONLY (§15, M-004): row → archived_memories, family kept.

Peewee-backed: fetch via ORM fetch_row, archive INSERT + DELETE via the
ORM ArchivedMemory/Memory models in one atomic move.
"""
from __future__ import annotations

import sqlite3

from memo import db as memo_db
from memo import temporal
from memo.memory.common import fetch_row
from memo.models import orm as orm_models
from memo.models.orm import ArchivedMemory as OrmArchived
from memo.models.orm import Memory as OrmMemory


def remove(conn: sqlite3.Connection, key: str,
           namespace: str = "default") -> dict:
    row = fetch_row(conn, key, namespace)
    if row is None:
        return {"key": key, "action": "not_found"}
    now = temporal.now_iso()
    embedding = row["embedding"]
    if isinstance(embedding, (memoryview, bytearray)):
        embedding = bytes(embedding)
    peewee_db = memo_db.orm_db(conn)
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        OrmArchived.create(
            key=row["key"], namespace=namespace, value=row["value"],
            type=row["type"], embedding=embedding,
            stability=row["stability"], score=row["score"], uses=row["uses"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            valid_from=row["valid_from"], valid_to=row["valid_to"],
            valid_until=row["valid_until"], agent_id=row["agent_id"],
            status=row["status"], family_id=row["family_id"],
            superseded_by=row["superseded_by"],
            relation_type=row["relation_type"],
            archived_at=now, archived_reason="deleted")
        (OrmMemory.delete()
         .where((OrmMemory.key == key) & (OrmMemory.namespace == namespace))
         .execute())
    return {"key": key, "action": "deleted"}
