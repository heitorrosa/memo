"""Memory domain: save ladder + search read path + soft remove (§§8-16).

Import rule (§37): imports temporal/rank/embeddings/db/errors — never skills.
NO cap logic here (L4 wires maintenance tails into save).
"""

from __future__ import annotations

import math
import re
import sqlite3
import uuid
import warnings

from rapidfuzz import fuzz

from memo import embeddings, errors, rank, temporal

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


def fetch_row(conn, key, namespace):
    record = conn.execute(
        "SELECT key, value, type, embedding, stability, score, uses,"
        " created_at, updated_at, valid_from, valid_to, status, family_id,"
        " superseded_by, relation_type FROM memories WHERE key=? AND namespace=?",
        (key, namespace)).fetchone()
    return dict(zip(
        ("key", "value", "type", "embedding", "stability", "score", "uses",
         "created_at", "updated_at", "valid_from", "valid_to", "status",
         "family_id", "superseded_by", "relation_type"), record)) if record else None


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


def save(conn: sqlite3.Connection, key: str, value: str, type: str = "context",
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

    old = fetch_row(conn, key, namespace)
    if old is not None:
        # Step 1: exact-key hit = UPDATE, never supersede (§8.1).
        value_embedding = embeddings.embed([value])[0]
        age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(old["created_at"])).total_seconds() / 86400)
        with conn:
            conn.execute(
                "UPDATE memories SET value=?, embedding=?, updated_at=?,"
                " uses=uses+1, score=score*1.1, stability=? WHERE key=? AND namespace=?",
                (value, value_embedding, max(now, old["updated_at"]),
                 grow_stability(old["stability"], old["type"], age), key, namespace))
        return {"key": key, "action": "updated"}

    # Step 2: fuzzy scan (current rows, same namespace; superseded excluded).
    candidates = conn.execute(
        "SELECT key, value FROM memories WHERE namespace=? AND status='current'",
        (namespace,)).fetchall()
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
            with conn:
                conn.execute(
                    "INSERT INTO memories(key,namespace,value,type,embedding,"
                    "stability,score,uses,created_at,updated_at,valid_from,"
                    "status,family_id,relation_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,"
                    "'current',?,'qualifies')",
                    (key, namespace, value, type, value_embedding, STABILITY[type], 1.0, 0,
                     now, now, effective_valid_from, family_id))
            return {"key": key, "action": "created"}
        elif outcome == "extends":
            value_embedding = embeddings.embed([value])[0]
            merged = value if hit_value in value else (hit_value if value in hit_value else hit_value + "; " + value)
            age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(hit_row["created_at"])).total_seconds() / 86400)
            with conn:
                conn.execute(
                    "UPDATE memories SET value=?, embedding=?, updated_at=?,"
                    " uses=uses+1, score=score*1.1, stability=? WHERE key=? AND namespace=?",
                    (merged, value_embedding, max(now, hit_row["updated_at"]),
                     grow_stability(hit_row["stability"], hit_row["type"], age), hit_key, namespace))
            return {"key": hit_key, "action": "updated"}
        elif outcome == "contradicts":
            relation = "corrects" if temporal.CORRECTION_PATTERN.search(value) else "supersedes"
            value_embedding = embeddings.embed([value])[0]
            with conn:
                conn.execute(
                    "UPDATE memories SET status='superseded', valid_to=?,"
                    " superseded_by=? WHERE key=? AND namespace=?",
                    (effective_valid_from, key, hit_key, namespace))
                conn.execute(
                    "INSERT INTO memories(key,namespace,value,type,embedding,"
                    "stability,score,uses,created_at,updated_at,valid_from,"
                    "status,family_id,relation_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,"
                    "'current',?,?)",
                    (key, namespace, value, type, value_embedding, STABILITY[type], 1.0, 0,
                     now, now, effective_valid_from, hit_row["family_id"], relation))
            return {"key": key, "action": "superseded"}
        else:  # reasserts: touch SAME row, new valid_from, never a new row
            age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(hit_row["created_at"])).total_seconds() / 86400)
            with conn:
                conn.execute(
                    "UPDATE memories SET updated_at=?, valid_from=?,"
                    " uses=uses+1, score=score*1.1, stability=? WHERE key=? AND namespace=?",
                    (max(now, hit_row["updated_at"]), effective_valid_from,
                     grow_stability(hit_row["stability"], hit_row["type"], age), hit_key, namespace))
            return {"key": hit_key, "action": "updated"}

    # Step 3: durability gate (§10) — reject trivia, SUCCESS shape, 0 rows.
    if is_trivia(value):
        return {"key": key, "action": "rejected_trivia"}

    # Step 4: insert (family=uuid4hex, valid_from unclamped — may be future).
    value_embedding = embeddings.embed([value])[0]
    with conn:
        conn.execute(
            "INSERT INTO memories(key,namespace,value,type,embedding,stability,"
            "score,uses,created_at,updated_at,valid_from,status,family_id)"
            " VALUES(?,?,?,?,?,?,1.0,0,?,?,?,'current',?)",
            (key, namespace, value, type, value_embedding, STABILITY[type], now, now, effective_valid_from,
             uuid.uuid4().hex))
    return {"key": key, "action": "created"}


def fts_scores(conn, query: str) -> dict[int, float]:
    query_tokens = [token for token in re.findall(r"[A-Za-z0-9_]+", query) if len(token) > 1]
    if not query_tokens:
        return {}
    match = " OR ".join(f'"{token}"' for token in query_tokens[:10])
    try:
        rows = conn.execute(
            "SELECT rowid, rank FROM memories_fts WHERE memories_fts MATCH ?"
            " ORDER BY rank LIMIT 500", (match,)).fetchall()
    except Exception:
        return {}
    if not rows:
        return {}
    top = rows[0][1]
    if not top:
        return {r: 1.0 for r, _ in rows}
    return {r: min(1.0, top / rk) if rk else 1.0 for r, rk in rows}


def search(conn: sqlite3.Connection, query: str, limit: int = 10,
           namespace: str = "default", valid_at: str = "now",
           as_known_at: str | None = None,
           include_superseded: bool = False) -> list[dict]:
    """Read path §14: embed→FTS→fuse→temporal BEFORE rank→filter→order→
    slice→touch-once. Returns [{key,value,type,score,age_days,uses}]."""
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


ARCHIVE_COLUMNS = ("key,namespace,value,type,embedding,stability,score,uses,"
              "created_at,updated_at,valid_from,valid_to,status,family_id,"
              "superseded_by,relation_type")


def remove(conn: sqlite3.Connection, key: str,
           namespace: str = "default") -> dict:
    """Soft-delete ONLY (§15, M-004): row → archived_memories, family kept."""
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


# L4 tail (Task 7, additive §8.5/§23): maintenance hook. call_start is captured BEFORE
# the write so the same-call sweep can never take this call's row (M-024).
# Logic above untouched; mutating receipts gain `archived` (victim key|None).
original_save = save


def save(conn: sqlite3.Connection, key: str, value: str, type: str = "context",
         namespace: str = "default", valid_from=None) -> dict:
    from memo import maintenance

    call_start = temporal.now_iso()
    receipt = original_save(conn, key, value, type=type, namespace=namespace,
                         valid_from=valid_from)
    maintenance_result = maintenance.run(conn, now=call_start)
    if receipt.get("action") in ("created", "updated", "superseded"):
        receipt["archived"] = maintenance_result["evicted"]
    return receipt
