"""Memory read path §14: embed→FTS→fuse→temporal BEFORE rank→filter→order→
slice→touch-once."""
from __future__ import annotations

import math
import sqlite3

from memo import embeddings, errors, rank, temporal
from memo.memory.common import BETA, R_FLOOR, STABILITY, S_CAP_X, fts_scores


def search(conn: sqlite3.Connection, query: str, limit: int = 10,
           namespace: str = "default", valid_at: str = "now",
           as_known_at: str | None = None,
           include_superseded: bool = False) -> list[dict]:
    """Returns [{key,value,type,score,age_days,uses}]."""
    if not query or not query.strip():
        return errors.err("empty_query", "query is empty")
    try:
        anchor_time = temporal.parse_ts(temporal.now_iso() if valid_at == "now" else valid_at)
    except ValueError:
        return errors.err("internal", f"bad valid_at {valid_at!r}")
    known = None
    if as_known_at is not None:
        try:
            known = temporal.parse_ts(as_known_at)
        except ValueError:
            return errors.err("internal", f"bad as_known_at {as_known_at!r}")
    limit = min(max(1, limit), 50)  # silent clamp, no error

    query_embedding = embeddings.embed([query])[0]
    fts_hits = fts_scores(conn, query)
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
    with conn:
        for result in scored:
            retention = math.exp(-max(0.0, result["age"]) / result["stab"])
            # M-006: superseded rows freeze R (no S growth); M-003 caps.
            new_stability = result["stab"] if result["status"] != "current" or retention < R_FLOOR else min(
                result["stab"] * (1.0 + BETA * retention), S_CAP_X * STABILITY[result["type"]])
            conn.execute(
                "UPDATE memories SET score=score*1.1, uses=uses+1,"
                " stability=?, updated_at=? WHERE key=? AND namespace=?",
                (new_stability, touch_now, result["key"], namespace))
            result.pop("stab"), result.pop("age"), result.pop("updated"), result.pop("created"), result.pop("status")
    return scored
