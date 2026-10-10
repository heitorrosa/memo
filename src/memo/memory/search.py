"""Memory read path §14: embed→FTS→fuse→temporal BEFORE rank→filter→order→
slice→touch-once.

Peewee-backed: FTS via MemoryFTS, touch UPDATEs via the ORM Memory model;
the 500-row scan stays raw SQL (ORM Memory exposes no rowid field and the
FTS score map is keyed by rowid — ORM cannot cover it). Cosine/fuse/order/
temporal predicates untouched.
"""
from __future__ import annotations

import math
import sqlite3

from memo import db as memo_db
from memo import embeddings, errors, rank, temporal
from memo import prompts as prompt_store
from memo.memory.common import BETA, R_FLOOR, STABILITY, S_CAP_X, fts_scores
from memo.models import orm as orm_models
from memo.models.orm import Memory as OrmMemory


def search(conn: sqlite3.Connection, query: str, limit: int = 10,
           namespace: str = "default", valid_at: str = "now",
           as_known_at: str | None = None,
           include_superseded: bool = False) -> list[dict]:
    """Returns [{key,value,type,score,age_days,uses}]."""
    if not query or not query.strip():
        return errors.err("empty_query", prompt_store.render("err_empty_query"))
    try:
        anchor_time = temporal.parse_ts(temporal.now_iso() if valid_at == "now" else valid_at)
    except ValueError:
        return errors.err("internal", prompt_store.render(
            "err_valid_at", valid_at=valid_at))
    known = None
    if as_known_at is not None:
        try:
            known = temporal.parse_ts(as_known_at)
        except ValueError:
            return errors.err("internal", prompt_store.render(
                "err_as_known_at", as_known_at=as_known_at))
    limit = min(max(1, limit), 50)  # silent clamp, no error

    query_embedding = embeddings.embed([query])[0]
    fts_hits = fts_scores(conn, query)
    # raw: rowid needed for the FTS map; ORM Memory has no rowid field.
    id_rows = conn.execute(
        "SELECT rowid, key, value, type, embedding, stability, score, uses,"
        " created_at, updated_at, valid_from, valid_to, status, family_id"
        " FROM memories WHERE namespace=? LIMIT 500", (namespace,)).fetchall()

    now = temporal.parse_ts(temporal.now_iso())
    scored = []
    for row_id, key, value, kind, embedding, stability, score, uses, created_at, updated_at, valid_from, valid_to, status, family_id in id_rows:
        row = {"status": status, "valid_from": valid_from, "valid_to": valid_to}
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
        # Belief axis: an explicit as_known_at evaluates the window as of K
        # unless the caller also pinned a valid_at question (T-then wins).
        query_time = anchor_time if (valid_at != "now" or known is None) else known
        if include_superseded:
            if not temporal.started(row, query_time):
                continue
        elif status == "current":
            if not temporal.in_window(row, query_time):
                continue
        elif known is not None and valid_to_parsed is not None and valid_to_parsed > known \
                and temporal.in_window(row, query_time):
            pass  # current-at-K: memo believed it current then
        else:
            continue
        vector_score = embeddings.cosine(query_embedding, embedding)
        text_score = fts_hits.get(row_id, 0.0)  # no-hit 0, no renormalization (M-002)
        age = max(0.0, (now - temporal.parse_ts(created_at)).total_seconds() / 86400)
        scored.append({
            "key": key, "value": value, "type": kind, "score": rank.fuse(vector_score, text_score, age, stability),
            "age_days": int(age), "uses": uses + 1,
            "stab": stability, "age": age, "updated": updated_at, "created": created_at, "status": status})

    scored = rank.order(scored)[:limit]
    touch_now = temporal.now_iso()
    peewee_db = memo_db.orm_db(conn)
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        for result in scored:
            retention = math.exp(-max(0.0, result["age"]) / result["stab"])
            # M-006: superseded rows freeze R (no S growth); M-003 caps.
            new_stability = result["stab"] if result["status"] != "current" or retention < R_FLOOR else min(
                result["stab"] * (1.0 + BETA * retention), S_CAP_X * STABILITY[result["type"]])
            (OrmMemory.update(score=OrmMemory.score * 1.1, uses=OrmMemory.uses + 1,
                              stability=new_stability, updated_at=touch_now)
             .where((OrmMemory.key == result["key"]) & (OrmMemory.namespace == namespace))
             .execute())
            result.pop("stab"), result.pop("age"), result.pop("updated"), result.pop("created"), result.pop("status")
    return scored
