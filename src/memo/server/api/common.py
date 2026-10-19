from __future__ import annotations

from fastapi.responses import JSONResponse

from memo.memory.common import STABILITY

MEMORY_COLUMNS = (
    "key, namespace, value, type, stability, score, uses, status, agent_id,"
    " family_id, relation_type, superseded_by, created_at, updated_at,"
    " valid_from, valid_to, valid_until"
)
SORTS = ("updated_at", "uses", "score")
STATUSES = ("current", "superseded")
STATUS_BY_CODE = {"not_found": 404, "cap_evicted": 409, "internal": 500}
TYPE_PATTERN = "^(" + "|".join(STABILITY) + ")$"
STATUS_PATTERN = "^(" + "|".join(STATUSES) + ")$"
SORT_PATTERN = "^(" + "|".join(SORTS) + ")$"


def status_for(code: str) -> int:
    return STATUS_BY_CODE.get(code, 400)


def error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}},
                        status_code=status)


def op_error(out: dict) -> JSONResponse:
    code = out.get("error") or "internal"
    return error(code, out.get("detail") or code, status_for(code))


def op_response(out):
    if isinstance(out, dict) and "error" in out:
        return op_error(out)
    return JSONResponse(out)


def raw(conn, sql: str, params=()) -> list[dict]:
    cursor = conn.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def degrees(conn, keys, namespace: str) -> dict[str, int]:
    keys = list(keys)
    if not keys:
        return {}
    marks = ",".join("?" * len(keys))
    rows = conn.execute(
        f"SELECT src_key, dst_key FROM edges WHERE namespace=?"
        f" AND (src_key IN ({marks}) OR dst_key IN ({marks}))",
        (namespace, *keys, *keys)).fetchall()
    wanted = set(keys)
    counts = {key: 0 for key in keys}
    for src, dst in rows:
        if src in wanted:
            counts[src] += 1
        if dst in wanted and dst != src:
            counts[dst] += 1
    return counts


def memory_json(row: dict, degree: int) -> dict:
    out = dict(row)
    out["degree"] = degree
    return out


def memory_dict(row) -> dict:
    return {
        "key": row.key, "namespace": row.namespace, "value": row.value,
        "type": row.type, "stability": row.stability, "score": row.score,
        "uses": row.uses, "status": row.status, "agent_id": row.agent_id,
        "family_id": row.family_id, "relation_type": row.relation_type,
        "superseded_by": row.superseded_by, "created_at": row.created_at,
        "updated_at": row.updated_at, "valid_from": row.valid_from,
        "valid_to": row.valid_to, "valid_until": row.valid_until,
    }


def names(blob: str | None) -> list[str]:
    return [name for name in (blob or "").split(",") if name]
