"""Memory shared constants + row helpers (spec §§8-16).

Peewee-backed: fetch_row reads through the ORM Memory model (bound by
db.orm_ctx to conn's own file); constants/predicates untouched.
"""
from __future__ import annotations

import math
import re
import sqlite3

from peewee import SQL

from memo import db as memo_db
from memo import embeddings, temporal
from memo.models.orm import Memory as OrmMemory
from memo.models.orm import MemoryFTS

STABILITY = {"preference": 14.0, "feedback": 10.0, "analysis": 7.0, "context": 3.0}
BETA, R_FLOOR, S_CAP_X = 0.3, 0.05, 4.0
FUZZ_HIT = 80

ACK_PHRASES = frozenset({
    "hi", "hello", "hey", "thanks", "thank you", "bye", "goodbye", "ok",
    "okay", "got it", "sure", "yes", "no", "yep", "nope", "cool", "great",
    "nice", "acknowledged", "noted", "will do", "sounds good", "roger",
    "copy", "understood", "fine", "alright", "k", "kk", "thx", "ty",
    "cheers", "cya", "see you", "later", "good morning", "good night",
    "morning", "night", "welcome",
})
DURABLE_NOUN_PATTERN = re.compile(
    r"prefer|like|hate|love|use[sd]? |stack|project|deploy|editor|convention|"
    r"credential|feedback|analysis|freeze|commit|path|convention|workflow|"
    r"decid|believe|live[sd]? in|work[sd]? (?:at|on|in)|allergic|goal",
    re.I)
SHELL_EXIT_PATTERN = re.compile(r"exit(?:\s*code)?\s*[:=]?\s*\d+", re.I)

MEMORY_COLUMNS = ("key value type embedding stability score uses created_at updated_at "
         "valid_from valid_to status family_id superseded_by relation_type".split())

ARCHIVE_COLUMNS = ("key,namespace,value,type,embedding,stability,score,uses,"
              "created_at,updated_at,valid_from,valid_to,status,family_id,"
              "superseded_by,relation_type")


def fetch_row(conn, key, namespace):
    with memo_db.orm_ctx(conn):
        row = OrmMemory.get_or_none(
            (OrmMemory.key == key) & (OrmMemory.namespace == namespace))
    if row is None:
        return None
    embedding = row.embedding
    if isinstance(embedding, (memoryview, bytearray)):
        embedding = bytes(embedding)
    return {
        "key": row.key, "value": row.value, "type": row.type,
        "embedding": embedding, "stability": row.stability,
        "score": row.score, "uses": row.uses,
        "created_at": row.created_at, "updated_at": row.updated_at,
        "valid_from": row.valid_from, "valid_to": row.valid_to,
        "status": row.status, "family_id": row.family_id,
        "superseded_by": row.superseded_by,
        "relation_type": row.relation_type,
    }


def is_trivia(value: str) -> bool:
    normalized = re.sub(r"[^\w\s]", "", value.strip().lower())
    if len(value.strip()) < 3 or normalized in ACK_PHRASES:
        return True
    if (value.lstrip().startswith(("$", ">")) and len(value) > 200
            and SHELL_EXIT_PATTERN.search(value) and not DURABLE_NOUN_PATTERN.search(value)):
        return True
    return False


def grow_stability(stability: float, kind: str, age_days: float) -> float:
    retention = math.exp(-max(0.0, age_days) / stability)
    if retention < R_FLOOR:
        return stability
    return min(stability * (1.0 + BETA * retention), S_CAP_X * STABILITY[kind])


def fts_scores(conn, query: str) -> dict[int, float]:
    query_tokens = [token for token in re.findall(r"[A-Za-z0-9_]+", query) if len(token) > 1]
    if not query_tokens:
        return {}
    match = " OR ".join(f'"{token}"' for token in query_tokens[:10])
    try:
        with memo_db.orm_ctx(conn):
            rows = list((MemoryFTS.select(MemoryFTS.rowid, MemoryFTS.rank().alias("rank_value"))
                         .where(MemoryFTS.match(match))
                         .order_by(SQL("rank_value")).limit(500).tuples()).execute())
    except Exception:
        return {}
    if not rows:
        return {}
    top = rows[0][1]
    if not top:
        return {row_id: 1.0 for row_id, _ in rows}
    return {row_id: min(1.0, top / rank_value) if rank_value else 1.0 for row_id, rank_value in rows}
