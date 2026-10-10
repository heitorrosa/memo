"""Memory writes: 5-step save ladder + L4 maintenance tail (§8 order IS semantics).

Peewee-backed: reads/writes go through the ORM Memory model bound to
conn's own file (db.orm_db); the fuzzy loop, temporal classify, BLOB
embeddings, receipts, and tail order are untouched.
"""
from __future__ import annotations

import sqlite3
import uuid
import warnings

from rapidfuzz import fuzz

from memo import db as memo_db
from memo import embeddings, temporal
from memo.memory.common import (
    FUZZ_HIT,
    STABILITY,
    fetch_row,
    grow_stability,
    is_trivia,
)
from memo.models import orm as orm_models
from memo.models.orm import Memory as OrmMemory


def original_save(conn: sqlite3.Connection, key: str, value: str, type: str = "context",
         namespace: str = "default", valid_from=None) -> dict:
    """5-step ladder (§8 order IS semantics). Receipts: created|updated|
    superseded|rejected_trivia (archived wired by L4)."""
    now = temporal.now_iso()
    if len(value) > 4096:
        warnings.warn("memory value large (>4KB), stored whole; truncation is display-only")
    effective_valid_from = temporal.coerce_date(valid_from) if valid_from else now
    try:
        temporal.parse_ts(effective_valid_from)
    except ValueError:
        effective_valid_from = now

    peewee_db = memo_db.orm_db(conn)
    old = fetch_row(conn, key, namespace)
    if old is not None:
        # Step 1: exact-key hit = UPDATE, never supersede (§8.1).
        value_embedding = embeddings.embed([value])[0]
        age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(old["created_at"])).total_seconds() / 86400)
        with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
            (OrmMemory.update(
                value=value, embedding=value_embedding,
                updated_at=max(now, old["updated_at"]),
                uses=OrmMemory.uses + 1, score=OrmMemory.score * 1.1,
                stability=grow_stability(old["stability"], old["type"], age))
             .where((OrmMemory.key == key) & (OrmMemory.namespace == namespace))
             .execute())
        return {"key": key, "action": "updated"}

    # Step 2: fuzzy scan (current rows, same namespace; superseded excluded).
    with memo_db.orm_ctx(conn):
        candidates = list((OrmMemory.select(OrmMemory.key, OrmMemory.value)
                           .where((OrmMemory.namespace == namespace)
                                  & (OrmMemory.status == "current"))
                           .tuples()))
    hit = None
    for candidate_key, candidate_value in candidates:
        if fuzz.ratio(key, candidate_key) >= FUZZ_HIT:
            hit = (candidate_key, candidate_value)
            break
    if hit is not None:
        hit_key, hit_value = hit
        hit_row = fetch_row(conn, hit_key, namespace)
        outcome = temporal.classify(hit_value, key, value)
        if outcome == "unrelated":
            pass  # fall through to insert (NEW family)
        elif outcome == "qualifies":
            family_id = hit_row["family_id"]
            value_embedding = embeddings.embed([value])[0]
            with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
                OrmMemory.create(
                    key=key, namespace=namespace, value=value, type=type,
                    embedding=value_embedding, stability=STABILITY[type],
                    score=1.0, uses=0, created_at=now, updated_at=now,
                    valid_from=effective_valid_from, valid_to=None,
                    status="current", family_id=family_id,
                    superseded_by=None, relation_type="qualifies")
            return {"key": key, "action": "created"}
        elif outcome == "extends":
            value_embedding = embeddings.embed([value])[0]
            merged = value if hit_value in value else (hit_value if value in hit_value else hit_value + "; " + value)
            age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(hit_row["created_at"])).total_seconds() / 86400)
            with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
                (OrmMemory.update(
                    value=merged, embedding=value_embedding,
                    updated_at=max(now, hit_row["updated_at"]),
                    uses=OrmMemory.uses + 1, score=OrmMemory.score * 1.1,
                    stability=grow_stability(hit_row["stability"], hit_row["type"], age))
                 .where((OrmMemory.key == hit_key) & (OrmMemory.namespace == namespace))
                 .execute())
            return {"key": hit_key, "action": "updated"}
        elif outcome == "contradicts":
            relation = "corrects" if temporal.CORRECTION_PATTERN.search(value) else "supersedes"
            value_embedding = embeddings.embed([value])[0]
            with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
                (OrmMemory.update(status="superseded", valid_to=effective_valid_from,
                                  superseded_by=key)
                 .where((OrmMemory.key == hit_key) & (OrmMemory.namespace == namespace))
                 .execute())
                OrmMemory.create(
                    key=key, namespace=namespace, value=value, type=type,
                    embedding=value_embedding, stability=STABILITY[type],
                    score=1.0, uses=0, created_at=now, updated_at=now,
                    valid_from=effective_valid_from, valid_to=None,
                    status="current", family_id=hit_row["family_id"],
                    superseded_by=None, relation_type=relation)
            return {"key": key, "action": "superseded"}
        else:  # reasserts: touch SAME row, new valid_from, never a new row
            age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(hit_row["created_at"])).total_seconds() / 86400)
            with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
                (OrmMemory.update(
                    updated_at=max(now, hit_row["updated_at"]),
                    valid_from=effective_valid_from,
                    uses=OrmMemory.uses + 1, score=OrmMemory.score * 1.1,
                    stability=grow_stability(hit_row["stability"], hit_row["type"], age))
                 .where((OrmMemory.key == hit_key) & (OrmMemory.namespace == namespace))
                 .execute())
            return {"key": hit_key, "action": "updated"}

    # Step 3: durability gate (§10) — reject trivia, SUCCESS shape, 0 rows.
    if is_trivia(value):
        return {"key": key, "action": "rejected_trivia"}

    # Step 4: insert (family=uuid4hex, valid_from unclamped — may be future).
    value_embedding = embeddings.embed([value])[0]
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        OrmMemory.create(
            key=key, namespace=namespace, value=value, type=type,
            embedding=value_embedding, stability=STABILITY[type],
            score=1.0, uses=0, created_at=now, updated_at=now,
            valid_from=effective_valid_from, valid_to=None,
            status="current", family_id=uuid.uuid4().hex,
            superseded_by=None, relation_type=None)
    return {"key": key, "action": "created"}


# L4 tail (Task 7, additive §8.5/§23): maintenance hook. call_start is captured
# BEFORE the write so the same-call sweep can never take this call's row
# (M-024). Logic above untouched; mutating receipts gain `archived`
# (victim key|None).
save = original_save


def save(conn: sqlite3.Connection, key: str, value: str, type: str = "context",  # noqa: F811 — tail wraps the ladder above
         namespace: str = "default", valid_from=None) -> dict:
    from memo import maintenance

    call_start = temporal.now_iso()
    receipt = original_save(conn, key, value, type=type, namespace=namespace,
                         valid_from=valid_from)
    maintenance_result = maintenance.run(conn, now=call_start)
    if receipt.get("action") in ("created", "updated", "superseded"):
        receipt["archived"] = maintenance_result["evicted"]
    return receipt
