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

_ACK = frozenset({
    "hi", "hello", "hey", "thanks", "thank you", "bye", "goodbye", "ok",
    "okay", "got it", "sure", "yes", "no", "yep", "nope", "cool", "great",
    "nice", "acknowledged", "noted", "will do", "sounds good", "roger",
    "copy", "understood", "fine", "alright", "k", "kk", "thx", "ty",
    "cheers", "cya", "see you", "later", "good morning", "good night",
    "morning", "night", "welcome",
})
_DURABLE_NOUN = re.compile(
    r"prefer|like|hate|love|use[sd]? |stack|project|deploy|editor|convention|"
    r"credential|feedback|analysis|freeze|commit|path|convention|workflow|"
    r"decid|believe|live[sd]? in|work[sd]? (?:at|on|in)|allergic|goal",
    re.I)
_SHELL_EXIT = re.compile(r"exit(?:\s*code)?\s*[:=]?\s*\d+", re.I)

_COLS = ("key value type embedding stability score uses created_at updated_at "
         "valid_from valid_to status family_id superseded_by relation_type".split())


def _row(conn, key, namespace):
    r = conn.execute(
        "SELECT key, value, type, embedding, stability, score, uses,"
        " created_at, updated_at, valid_from, valid_to, status, family_id,"
        " superseded_by, relation_type FROM memories WHERE key=? AND namespace=?",
        (key, namespace)).fetchone()
    return dict(zip(
        ("key", "value", "type", "embedding", "stability", "score", "uses",
         "created_at", "updated_at", "valid_from", "valid_to", "status",
         "family_id", "superseded_by", "relation_type"), r)) if r else None


def _is_trivia(value: str) -> bool:
    norm = re.sub(r"[^\w\s]", "", value.strip().lower())
    if len(value.strip()) < 3 or norm in _ACK:
        return True
    if (value.lstrip().startswith(("$", ">")) and len(value) > 200
            and _SHELL_EXIT.search(value) and not _DURABLE_NOUN.search(value)):
        return True
    return False


def _grow(stability: float, stype: str, age_days: float) -> float:
    r = math.exp(-max(0.0, age_days) / stability)
    if r < R_FLOOR:
        return stability
    return min(stability * (1.0 + BETA * r), S_CAP_X * STABILITY[stype])


def save(conn: sqlite3.Connection, key: str, value: str, type: str = "context",
         namespace: str = "default", valid_from=None) -> dict:
    """5-step ladder (§8 order IS semantics). Receipts: created|updated|
    superseded|rejected_trivia (archived wired by L4)."""
    now = temporal.now_iso()
    if len(value) > 4096:
        warnings.warn("memory value large (>4KB), stored whole; truncation is display-only")
    vf = temporal._coerce(valid_from) if valid_from else now
    try:
        temporal.parse_ts(vf)
    except ValueError:
        vf = now

    old = _row(conn, key, namespace)
    if old is not None:
        # Step 1: exact-key hit = UPDATE, never supersede (§8.1).
        emb = embeddings.embed([value])[0]
        age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(old["created_at"])).total_seconds() / 86400)
        with conn:
            conn.execute(
                "UPDATE memories SET value=?, embedding=?, updated_at=?,"
                " uses=uses+1, score=score*1.1, stability=? WHERE key=? AND namespace=?",
                (value, emb, max(now, old["updated_at"]),
                 _grow(old["stability"], old["type"], age), key, namespace))
        return {"key": key, "action": "updated"}

    # Step 2: fuzzy scan (current rows, same namespace; superseded excluded).
    cands = conn.execute(
        "SELECT key, value FROM memories WHERE namespace=? AND status='current'",
        (namespace,)).fetchall()
    hit = None
    for ck, cv in cands:
        if fuzz.ratio(key, ck) >= FUZZ_HIT:
            hit = (ck, cv)
            break
    if hit is not None:
        hk, hv = hit
        hrow = _row(conn, hk, namespace)
        outcome = temporal.classify(hv, key, value)
        if outcome == "unrelated":
            pass  # fall through to insert (NEW family)
        elif outcome == "qualifies":
            fam = hrow["family_id"]
            emb = embeddings.embed([value])[0]
            with conn:
                conn.execute(
                    "INSERT INTO memories(key,namespace,value,type,embedding,"
                    "stability,score,uses,created_at,updated_at,valid_from,"
                    "status,family_id,relation_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,"
                    "'current',?,'qualifies')",
                    (key, namespace, value, type, emb, STABILITY[type], 1.0, 0,
                     now, now, vf, fam))
            return {"key": key, "action": "created"}
        elif outcome == "extends":
            emb = embeddings.embed([value])[0]
            merged = value if hv in value else (hv if value in hv else hv + "; " + value)
            age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(hrow["created_at"])).total_seconds() / 86400)
            with conn:
                conn.execute(
                    "UPDATE memories SET value=?, embedding=?, updated_at=?,"
                    " uses=uses+1, score=score*1.1, stability=? WHERE key=? AND namespace=?",
                    (merged, emb, max(now, hrow["updated_at"]),
                     _grow(hrow["stability"], hrow["type"], age), hk, namespace))
            return {"key": hk, "action": "updated"}
        elif outcome == "contradicts":
            rel = "corrects" if temporal._CORRECT.search(value) else "supersedes"
            emb = embeddings.embed([value])[0]
            with conn:
                conn.execute(
                    "UPDATE memories SET status='superseded', valid_to=?,"
                    " superseded_by=? WHERE key=? AND namespace=?",
                    (vf, key, hk, namespace))
                conn.execute(
                    "INSERT INTO memories(key,namespace,value,type,embedding,"
                    "stability,score,uses,created_at,updated_at,valid_from,"
                    "status,family_id,relation_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,"
                    "'current',?,?)",
                    (key, namespace, value, type, emb, STABILITY[type], 1.0, 0,
                     now, now, vf, hrow["family_id"], rel))
            return {"key": key, "action": "superseded"}
        else:  # reasserts: touch SAME row, new valid_from, never a new row
            age = max(0.0, (temporal.parse_ts(now) - temporal.parse_ts(hrow["created_at"])).total_seconds() / 86400)
            with conn:
                conn.execute(
                    "UPDATE memories SET updated_at=?, valid_from=?,"
                    " uses=uses+1, score=score*1.1, stability=? WHERE key=? AND namespace=?",
                    (max(now, hrow["updated_at"]), vf,
                     _grow(hrow["stability"], hrow["type"], age), hk, namespace))
            return {"key": hk, "action": "updated"}

    # Step 3: durability gate (§10) — reject trivia, SUCCESS shape, 0 rows.
    if _is_trivia(value):
        return {"key": key, "action": "rejected_trivia"}

    # Step 4: insert (family=uuid4hex, valid_from unclamped — may be future).
    emb = embeddings.embed([value])[0]
    with conn:
        conn.execute(
            "INSERT INTO memories(key,namespace,value,type,embedding,stability,"
            "score,uses,created_at,updated_at,valid_from,status,family_id)"
            " VALUES(?,?,?,?,?,?,1.0,0,?,?,?,'current',?)",
            (key, namespace, value, type, emb, STABILITY[type], now, now, vf,
             uuid.uuid4().hex))
    return {"key": key, "action": "created"}


def _fts_scores(conn, query: str) -> dict[int, float]:
    toks = [t for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 1]
    if not toks:
        return {}
    match = " OR ".join(f'"{t}"' for t in toks[:10])
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
        at = temporal.parse_ts(temporal.now_iso() if valid_at == "now" else valid_at)
    except ValueError:
        return errors.err("internal", f"bad valid_at {valid_at!r}")
    known = None
    if as_known_at is not None:
        try:
            known = temporal.parse_ts(as_known_at)
        except ValueError:
            return errors.err("internal", f"bad as_known_at {as_known_at!r}")
    limit = min(max(1, limit), 50)  # silent clamp, no error

    qemb = embeddings.embed([query])[0]
    fts = _fts_scores(conn, query)
    id_rows = conn.execute(
        "SELECT rowid, key, value, type, embedding, stability, score, uses,"
        " created_at, updated_at, valid_from, valid_to, status, family_id"
        " FROM memories WHERE namespace=? LIMIT 500", (namespace,)).fetchall()

    now = temporal.parse_ts(temporal.now_iso())
    scored = []
    for rid, k, v, t, emb, stab, score, uses, ca, ua, vf, vt, st, fam in id_rows:
        row = {"status": st, "valid_from": vf, "valid_to": vt}
        try:
            vto = temporal.parse_ts(vt) if vt is not None else None
        except ValueError:
            continue
        if known is not None:
            try:
                if temporal.parse_ts(ca) > known:
                    continue
            except ValueError:
                continue
        # Belief axis: an explicit as_known_at evaluates the window as of K
        # unless the caller also pinned a valid_at question (T-then wins).
        Tq = at if (valid_at != "now" or known is None) else known
        if include_superseded:
            if not temporal.started(row, Tq):
                continue
        elif st == "current":
            if not temporal.in_window(row, Tq):
                continue
        elif known is not None and vto is not None and vto > known \
                and temporal.in_window(row, Tq):
            pass  # current-at-K: memo believed it current then
        else:
            continue
        V = embeddings.cosine(qemb, emb)
        T = fts.get(rid, 0.0)  # no-hit T=0, no renormalization (M-002)
        age = max(0.0, (now - temporal.parse_ts(ca)).total_seconds() / 86400)
        scored.append({
            "key": k, "value": v, "type": t, "score": rank.fuse(V, T, age, stab),
            "age_days": int(age), "uses": uses + 1,
            "_stab": stab, "_age": age, "_ua": ua, "_ca": ca, "_st": st})

    scored = rank.order(scored)[:limit]
    touch_now = temporal.now_iso()
    with conn:
        for r in scored:
            rr = math.exp(-max(0.0, r["_age"]) / r["_stab"])
            # M-006: superseded rows freeze R (no S growth); M-003 caps.
            ns = r["_stab"] if r["_st"] != "current" or rr < R_FLOOR else min(
                r["_stab"] * (1.0 + BETA * rr), S_CAP_X * STABILITY[r["type"]])
            conn.execute(
                "UPDATE memories SET score=score*1.1, uses=uses+1,"
                " stability=?, updated_at=? WHERE key=? AND namespace=?",
                (ns, touch_now, r["key"], namespace))
            r.pop("_stab"), r.pop("_age"), r.pop("_ua"), r.pop("_ca"), r.pop("_st")
    return scored


_ARCH_COLS = ("key,namespace,value,type,embedding,stability,score,uses,"
              "created_at,updated_at,valid_from,valid_to,status,family_id,"
              "superseded_by,relation_type")


def remove(conn: sqlite3.Connection, key: str,
           namespace: str = "default") -> dict:
    """Soft-delete ONLY (§15, M-004): row → archived_memories, family kept."""
    row = _row(conn, key, namespace)
    if row is None:
        return {"key": key, "action": "not_found"}
    now = temporal.now_iso()
    with conn:
        conn.execute(
            f"INSERT INTO archived_memories({_ARCH_COLS},archived_at,"
            f"archived_reason) VALUES({','.join('?' for _ in range(16))},?,?)",
            (row["key"], namespace, row["value"], row["type"], row["embedding"],
             row["stability"], row["score"], row["uses"], row["created_at"],
             row["updated_at"], row["valid_from"], row["valid_to"],
             row["status"], row["family_id"], row["superseded_by"],
             row["relation_type"], now, "deleted"))
        conn.execute("DELETE FROM memories WHERE key=? AND namespace=?",
                     (key, namespace))
    return {"key": key, "action": "deleted"}
