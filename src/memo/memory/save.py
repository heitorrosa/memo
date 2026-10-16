from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
import warnings

from rapidfuzz import fuzz

from memo import db as memo_db
from memo import embeddings, temporal
from memo import errors
from memo import maintenance
from memo.memory.common import (
    DENSITY_GUIDE_CHARS,
    FUZZ_HIT,
    STABILITY,
    fetch_row,
    grow_stability,
    is_trivia,
)
from memo.memory.graph import record_declarations
from memo.models import orm as orm_models
from memo.models.orm import Memory as OrmMemory

DATE_IN_KEY_PATTERN = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")
VALUE_DATE_PREFIX_PATTERN = re.compile(r"\[\d{4}-\d{2}-\d{2}\]")


def fallback_agent_id(token: str | None, user_agent: str | None) -> str:
    """Stable server-minted declarer id for a save that carries no agent_id.

    Distinct client apps become distinct declarers, but one app cannot confirm
    itself: the same (token, user agent) always mints the same id, so a repeat
    save only grows evidence and never reaches the second-distinct-declarer
    confirmation. Both inputs are client-asserted (a caller can spoof its user
    agent); accepted low-stakes risk, since a forged declarer buys only a
    higher provisional weight on a relation it declared anyway.
    """
    material = f"{token or ''}|{user_agent or 'unknown'}"
    return "auto-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def style_suggestion(key: str, value: str) -> str | None:
    notes = []
    if key != key.lower() or any(ch.isspace() for ch in key):
        notes.append(
            "natural_title: use a lowercase type.slug key (prefix == type)")
    if DATE_IN_KEY_PATTERN.search(key):
        notes.append(
            "date_in_key: keep dates out of keys; set valid_until instead")
    if VALUE_DATE_PREFIX_PATTERN.match(value.strip()):
        notes.append(
            "value_date_prefix: move the [YYYY-MM-DD] date to"
            " valid_from/valid_until")
    return "; ".join(notes) if notes else None


def record_edges(conn: sqlite3.Connection, key: str, parent_key: str | None = None) -> None:
    """Emit graph edges for a written row (graph Phase 0).

    Additive only; INSERT OR IGNORE keeps re-saves idempotent. `derived_from`
    links a newly created key to the parent it came from (version/split) and is
    never emitted on an update, because an update creates no new key lineage.
    `mentions` links any dotted key to its leading segment, a VIRTUAL entity
    node (no entities table, no node row). Raw DML: INSERT OR IGNORE has no
    single-call ORM form; writes stay on the app connection.
    """
    now = temporal.now_iso()
    db = memo_db.orm_db(conn)
    if parent_key is not None and parent_key != key:
        db.execute_sql(
            "INSERT OR IGNORE INTO edges(src_key,dst_key,rel,weight,created_at)"
            " VALUES(?,?,'derived_from',1.0,?)", (key, parent_key, now))
    if "." in key:
        db.execute_sql(
            "INSERT OR IGNORE INTO edges(src_key,dst_key,rel,weight,created_at)"
            " VALUES(?,?,'mentions',1.0,?)", (key, key.split(".")[0], now))


def original_save(conn: sqlite3.Connection, key: str, value: str, type: str = "context",
         namespace: str = "default", valid_from=None, valid_until=None,
         agent_id=None) -> dict:
    if type not in STABILITY:
        return errors.err("invalid_input", f"bad type {type!r}")
    prefix = key.split(".")[0] if "." in key else None
    if (prefix is not None and prefix.lower() in STABILITY
            and prefix.lower() != type):
        return errors.err(
            "invalid_input", f"key prefix {prefix!r} != type {type!r}")
    style_note = style_suggestion(key, value)

    def with_advisory(receipt: dict) -> dict:
        if style_note:
            receipt["suggestion"] = style_note
        return receipt

    now = temporal.now_iso()
    if len(value) > 4096:
        warnings.warn("memory value large (>4KB), stored whole; truncation is display-only")
    effective_valid_from = temporal.coerce_date(valid_from) if valid_from else now
    try:
        temporal.parse_ts(effective_valid_from)
    except ValueError:
        effective_valid_from = now
    if valid_until:
        try:
            temporal.parse_ts(temporal.coerce_date(valid_until))
            effective_valid_until = temporal.coerce_date(valid_until)
        except ValueError:
            effective_valid_until = None
    else:
        effective_valid_until = None
    stamp = {"agent_id": agent_id} if agent_id is not None else {}
    refresh_expiry = {"valid_until": effective_valid_until} if valid_until is not None else {}

    peewee_db = memo_db.orm_db(conn)
    old = fetch_row(conn, key, namespace)
    if old is not None:
        value_embedding = embeddings.embed([value])[0]
        age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(old["created_at"])).total_seconds() / 86400)
        with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
            (OrmMemory.update(
                value=value, embedding=value_embedding,
                updated_at=max(now, old["updated_at"]),
                uses=OrmMemory.uses + 1, score=OrmMemory.score * 1.1,
                stability=grow_stability(old["stability"], old["type"], age),
                **stamp, **refresh_expiry)
             .where((OrmMemory.key == key) & (OrmMemory.namespace == namespace))
             .execute())
            record_edges(conn, key)
        return with_advisory({"key": key, "action": "updated"})

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
            pass
        elif outcome == "qualifies":
            family_id = hit_row["family_id"]
            value_embedding = embeddings.embed([value])[0]
            with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
                OrmMemory.create(
                    key=key, namespace=namespace, value=value, type=type,
                    embedding=value_embedding, stability=STABILITY[type],
                    score=1.0, uses=0, created_at=now, updated_at=now,
                    valid_from=effective_valid_from, valid_to=None,
                    valid_until=effective_valid_until, agent_id=agent_id,
                    status="current", family_id=family_id,
                    superseded_by=None, relation_type="qualifies")
                record_edges(conn, key, hit_key)
            return with_advisory({"key": key, "action": "created"})
        elif outcome == "extends":
            value_embedding = embeddings.embed([value])[0]
            merged = value if hit_value in value else (hit_value if value in hit_value else hit_value + "; " + value)
            age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(hit_row["created_at"])).total_seconds() / 86400)
            with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
                (OrmMemory.update(
                    value=merged, embedding=value_embedding,
                    updated_at=max(now, hit_row["updated_at"]),
                    uses=OrmMemory.uses + 1, score=OrmMemory.score * 1.1,
                    stability=grow_stability(hit_row["stability"], hit_row["type"], age),
                    **stamp, **refresh_expiry)
                 .where((OrmMemory.key == hit_key) & (OrmMemory.namespace == namespace))
                 .execute())
                record_edges(conn, hit_key)
            return with_advisory({"key": hit_key, "action": "updated"})
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
                    valid_until=effective_valid_until, agent_id=agent_id,
                    status="current", family_id=hit_row["family_id"],
                    superseded_by=None, relation_type=relation)
            record_edges(conn, key, hit_key)
            return with_advisory({"key": key, "action": "superseded"})
        else:
            age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(hit_row["created_at"])).total_seconds() / 86400)
            with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
                (OrmMemory.update(
                    updated_at=max(now, hit_row["updated_at"]),
                    valid_from=effective_valid_from,
                    uses=OrmMemory.uses + 1, score=OrmMemory.score * 1.1,
                    stability=grow_stability(hit_row["stability"], hit_row["type"], age),
                    **stamp, **refresh_expiry)
                 .where((OrmMemory.key == hit_key) & (OrmMemory.namespace == namespace))
                 .execute())
                record_edges(conn, hit_key)
            return with_advisory({"key": hit_key, "action": "updated"})

    if is_trivia(value):
        return {"key": key, "action": "rejected_trivia"}

    value_embedding = embeddings.embed([value])[0]
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        OrmMemory.create(
            key=key, namespace=namespace, value=value, type=type,
            embedding=value_embedding, stability=STABILITY[type],
            score=1.0, uses=0, created_at=now, updated_at=now,
            valid_from=effective_valid_from, valid_to=None,
            valid_until=effective_valid_until, agent_id=agent_id,
            status="current", family_id=uuid.uuid4().hex,
            superseded_by=None, relation_type=None)
        record_edges(conn, key)
    return with_advisory({"key": key, "action": "created"})


def save(conn: sqlite3.Connection, key: str, value: str, type: str = "context",
         namespace: str = "default", valid_from=None, valid_until=None,
         agent_id=None, links=None, aliases=None, token=None,
         user_agent=None) -> dict:
    call_start = temporal.now_iso()
    receipt = original_save(conn, key, value, type=type, namespace=namespace,
                         valid_from=valid_from, valid_until=valid_until,
                         agent_id=agent_id)
    if "error" in receipt:
        return receipt
    if receipt.get("action") in ("created", "updated", "superseded"):
        declarer = agent_id if agent_id else fallback_agent_id(token, user_agent)
        note = record_declarations(conn, receipt["key"], links, aliases, declarer)
        if note:
            if receipt.get("suggestion"):
                receipt["suggestion"] += "; " + note
            else:
                receipt["suggestion"] = note
    maintenance_result = maintenance.run(conn, now=call_start)
    if receipt.get("action") in ("created", "updated", "superseded"):
        receipt["archived"] = maintenance_result["evicted"]
        if len(value) > DENSITY_GUIDE_CHARS:
            dense = "too_dense: split this into smaller facts with separate saves"
            if receipt.get("suggestion"):
                receipt["suggestion"] += "; " + dense
            else:
                receipt["suggestion"] = dense
    return receipt
