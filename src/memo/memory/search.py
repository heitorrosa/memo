from __future__ import annotations

import math
import os
import sqlite3

from memo import db as memo_db
from memo import embeddings, errors, rank, temporal
from memo import prompts as prompt_store
from memo.memory.common import (
    BETA,
    R_FLOOR,
    STABILITY,
    S_CAP_X,
    fetch_row,
    fts_scores,
)
from memo.memory.graph import neighborhood
from memo.models import orm as orm_models
from memo.models.orm import Memory as OrmMemory

# Graph Phase 1: flag-gated neighbor expansion. Off (the default) must be
# byte-identical to the pre-expansion retrieval path, so the flag is read per
# request and the whole block is skipped when it is unset.
EXPAND_TOP_K = 5
EXPAND_DECAY = 0.6
EXPAND_MAX_ADDED = 20


def expand_enabled() -> bool:
    """MEMO_GRAPH_EXPAND, read per request (not at import) so tests can flip it."""
    return os.getenv("MEMO_GRAPH_EXPAND", "").strip().lower() in ("1", "true", "yes")


def expand_candidates(conn: sqlite3.Connection, scored: list[dict],
                      namespace: str) -> list[dict]:
    """Propose 1-hop neighbors of the top hits as extra candidates (Phase 1).

    Graph proposes, rank disposes: a neighbor enters the pool with
    source_score x stored_edge_weight x 0.6, dedup by key keeps the max score,
    and at most 20 neighbor keys are merged. `contradicts` edges are flag-only
    and never contribute. Pure read; the fusion formula is untouched.
    """
    by_key = {row["key"]: row for row in scored}
    proposed: dict[str, float] = {}
    for source in sorted(scored, key=lambda row: row["score"], reverse=True)[:EXPAND_TOP_K]:
        for neighbor in neighborhood(conn, source["key"], 1):
            if neighbor["rel"] == "contradicts":
                continue
            key = neighbor["key"]
            score = source["score"] * neighbor["weight"] * EXPAND_DECAY
            if key not in proposed or score > proposed[key]:
                proposed[key] = score
    at = temporal.parse_ts(temporal.now_iso())
    merged = 0
    for key, score in proposed.items():
        if merged >= EXPAND_MAX_ADDED:
            break
        existing = by_key.get(key)
        if existing is not None:
            merged += 1
            if score > existing["score"]:
                existing["score"] = score
            continue
        row = fetch_row(conn, key, namespace)
        if row is None:
            continue
        if temporal.is_expired(row, at) or not temporal.in_window(row, at):
            continue
        age = max(0.0, (at - temporal.parse_ts(row["created_at"])).total_seconds() / 86400)
        candidate = {
            "key": row["key"], "value": row["value"], "type": row["type"],
            "score": score, "age_days": int(age), "uses": row["uses"] + 1,
            "stab": row["stability"], "age": age, "updated": row["updated_at"],
            "created": row["created_at"], "status": row["status"],
        }
        by_key[key] = candidate
        scored.append(candidate)
        merged += 1
    return scored


def search(conn: sqlite3.Connection, query: str, limit: int = 10,
           namespace: str = "default", valid_at: str = "now",
           as_known_at: str | None = None,
           include_superseded: bool = False,
           type: str | None = None,
           min_score: float | None = None) -> list[dict]:
    if not query or not query.strip():
        return errors.err("empty_query", prompt_store.render("err_empty_query"))
    try:
        anchor_time = temporal.parse_ts(temporal.now_iso() if valid_at == "now" else valid_at)
    except ValueError:
        return errors.err("invalid_input", prompt_store.render(
            "err_valid_at", valid_at=valid_at))
    known = None
    if as_known_at is not None:
        try:
            known = temporal.parse_ts(as_known_at)
        except ValueError:
            return errors.err("invalid_input", prompt_store.render(
                "err_as_known_at", as_known_at=as_known_at))
    limit = min(max(1, limit), 50)

    query_embedding = embeddings.embed([query])[0]
    fts_hits = fts_scores(conn, query)
    id_rows = conn.execute(
        "SELECT rowid, key, value, type, embedding, stability, score, uses,"
        " created_at, updated_at, valid_from, valid_to, valid_until, status, family_id"
        " FROM memories WHERE namespace=? LIMIT 500", (namespace,)).fetchall()

    now = temporal.parse_ts(temporal.now_iso())
    scored = []
    for row_id, key, value, kind, embedding, stability, score, uses, created_at, updated_at, valid_from, valid_to, valid_until, status, family_id in id_rows:
        row = {"status": status, "valid_from": valid_from, "valid_to": valid_to,
               "valid_until": valid_until}
        try:
            valid_to_parsed = temporal.parse_ts(valid_to) if valid_to is not None else None
        except ValueError:
            continue
        if known is not None:
            try:
                if temporal.parse_ts(created_at) > known:
                    continue
            except ValueError:
                continue
        query_time = anchor_time if (valid_at != "now" or known is None) else known
        if temporal.is_expired(row, query_time):
            continue
        if include_superseded:
            if not temporal.started(row, query_time):
                continue
        elif status == "current":
            if not temporal.in_window(row, query_time):
                continue
        elif known is not None and valid_to_parsed is not None and valid_to_parsed > known \
                and temporal.in_window(row, query_time):
            pass
        else:
            continue
        vector_score = embeddings.cosine(query_embedding, embedding)
        text_score = fts_hits.get(row_id, 0.0)
        age = max(0.0, (now - temporal.parse_ts(created_at)).total_seconds() / 86400)
        scored.append({
            "key": key, "value": value, "type": kind, "score": rank.fuse(vector_score, text_score, age, stability),
            "age_days": int(age), "uses": uses + 1,
            "stab": stability, "age": age, "updated": updated_at, "created": created_at, "status": status})

    if expand_enabled():
        scored = expand_candidates(conn, scored, namespace)
    if type is not None:
        scored = [result for result in scored if result["type"] == type]
    if min_score is not None:
        scored = [result for result in scored if result["score"] >= min_score]
    scored = rank.order(scored)[:limit]
    touch_now = temporal.now_iso()
    peewee_db = memo_db.orm_db(conn)
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        for result in scored:
            retention = math.exp(-max(0.0, result["age"]) / result["stab"])
            new_stability = result["stab"] if result["status"] != "current" or retention < R_FLOOR else min(
                result["stab"] * (1.0 + BETA * retention), S_CAP_X * STABILITY[result["type"]])
            (OrmMemory.update(score=OrmMemory.score * 1.1, uses=OrmMemory.uses + 1,
                              stability=new_stability, updated_at=touch_now)
             .where((OrmMemory.key == result["key"]) & (OrmMemory.namespace == namespace))
             .execute())
            result.pop("stab"), result.pop("age"), result.pop("status")
            result.pop("created")
            result["updated_at"] = result.pop("updated")
    return scored
