"""Memory writes: 5-step save ladder + L4 maintenance tail (§8 order IS semantics).

Peewee-backed: reads/writes go through the ORM Memory model bound to
conn's own file (db.orm_db); the fuzzy loop, temporal classify, BLOB
embeddings, receipts, and tail order are untouched.
"""
from __future__ import annotations

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
from memo.models import orm as orm_models
from memo.models.orm import Memory as OrmMemory

DATE_IN_KEY_PATTERN = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")
VALUE_DATE_PREFIX_PATTERN = re.compile(r"\[\d{4}-\d{2}-\d{2}\]")


def style_suggestion(key: str, value: str) -> str | None:
    """Style advisories: the fact stores as-given, suggestion only.

    Never a rejection — mirrors the too_dense tail pattern (one suggestion
    string per receipt, multiple notes joined with '; ').
    """
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


def original_save(conn: sqlite3.Connection, key: str, value: str, type: str = "context",
         namespace: str = "default", valid_from=None, valid_until=None,
         agent_id=None) -> dict:
    """5-step ladder (§8 order IS semantics). Receipts: created|updated|
    superseded|rejected_trivia (archived wired by L4).

    valid_until is a user-set TTL (ISO-8601, coerced like valid_from);
    garbage means no expiry, never an error. agent_id stamps the writer on
    every mutating path; an omitted (None) stamp preserves the old one.

    Structure validates fail-fast (invalid_input, nothing stored): an unknown
    type, or a dotted prefix that itself names a type but mismatches it
    (preference.x saved as context). Dotted legacy prefixes (editor.*) and
    dotless natural titles stay writable through the transition so existing
    rows remain updatable. Style is advisory (suggestion on the receipt).
    """
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
    # Writer stamp + expiry refresh for UPDATE paths: present-only, so an
    # omitted argument preserves what the row already carries.
    stamp = {"agent_id": agent_id} if agent_id is not None else {}
    refresh_expiry = {"valid_until": effective_valid_until} if valid_until is not None else {}

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
                stability=grow_stability(old["stability"], old["type"], age),
                **stamp, **refresh_expiry)
             .where((OrmMemory.key == key) & (OrmMemory.namespace == namespace))
             .execute())
        return with_advisory({"key": key, "action": "updated"})

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
                    valid_until=effective_valid_until, agent_id=agent_id,
                    status="current", family_id=family_id,
                    superseded_by=None, relation_type="qualifies")
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
            return with_advisory({"key": key, "action": "superseded"})
        else:  # reasserts: touch SAME row, new valid_from, never a new row
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
            return with_advisory({"key": hit_key, "action": "updated"})

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
            valid_until=effective_valid_until, agent_id=agent_id,
            status="current", family_id=uuid.uuid4().hex,
            superseded_by=None, relation_type=None)
    return with_advisory({"key": key, "action": "created"})


# L4 tail (Task 7, additive §8.5/§23): maintenance hook. call_start is captured
# BEFORE the write so the same-call sweep can never take this call's row
# (M-024). Logic above untouched; mutating receipts gain `archived`
# (victim key|None).
def save(conn: sqlite3.Connection, key: str, value: str, type: str = "context",  # noqa: F811 — tail wraps the ladder above
         namespace: str = "default", valid_from=None, valid_until=None,
         agent_id=None) -> dict:
    call_start = temporal.now_iso()
    receipt = original_save(conn, key, value, type=type, namespace=namespace,
                         valid_from=valid_from, valid_until=valid_until,
                         agent_id=agent_id)
    if "error" in receipt:
        return receipt
    maintenance_result = maintenance.run(conn, now=call_start)
    if receipt.get("action") in ("created", "updated", "superseded"):
        receipt["archived"] = maintenance_result["evicted"]
        # Density is advisory, never a rejection: a past-guide value still
        # lands, with a suggestion to split it across saves. Appends when
        # the ladder already attached a style suggestion.
        if len(value) > DENSITY_GUIDE_CHARS:
            dense = "too_dense: split this into smaller facts with separate saves"
            if receipt.get("suggestion"):
                receipt["suggestion"] += "; " + dense
            else:
                receipt["suggestion"] = dense
    return receipt
