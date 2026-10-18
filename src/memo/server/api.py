"""Same-origin JSON API in front of the memo operations (contract §3).

Reads go through the peewee models (raw SELECTs only for counts, degree and
paging); every write goes through ``memo.server.operations.do_*`` under the
server lock, so the single-writer rule holds.
"""

from __future__ import annotations

import json
import sys
import threading
import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from memo import db as memo_db
from memo import embeddings
from memo.memory.common import STABILITY
from memo.models.orm import (
    Agent,
    AgentState,
    Alias,
    Edge,
    EdgeDeclarer,
    Memory,
    Skill,
)
from memo.server.operations import (
    do_memory_delete,
    do_memory_save,
    do_memory_search,
    do_skill_get,
    do_skill_put,
    do_skill_update,
)
from memo.server.shared import VERSION, internal_error
from memo.skills.crud import split_revisions

MEMORY_COLUMNS = (
    "key, namespace, value, type, stability, score, uses, status, agent_id,"
    " family_id, relation_type, superseded_by, created_at, updated_at,"
    " valid_from, valid_to, valid_until"
)
SORTS = ("updated_at", "uses", "score")
STATUSES = ("current", "superseded")
STATUS_BY_CODE = {"not_found": 404, "cap_evicted": 409, "internal": 500}

# Similarity graph: k nearest neighbours by cosine over the stored embeddings.
SIMILAR_TOP_K = 3
SIMILAR_FLOOR = 0.35
SIMILAR_RETRY_FLOOR = 0.25
SIMILAR_MAX = 400
SIMILAR_TTL = 30.0
SIMILAR_BUDGET = 1.5
RELATED_TOP_K = 5
RELATED_FLOOR = 0.25

# namespace -> (data stamp, expiry, pairs); stamp changes when a memory is
# written, so the table is rebuilt instead of served stale.
_sim_cache: dict[str, tuple] = {}
_sim_lock = threading.Lock()


class BadInput(Exception):
    """Request could not be parsed; answered with 400 invalid_input."""


def _status_for(code: str) -> int:
    return STATUS_BY_CODE.get(code, 400)


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}},
                        status_code=status)


def _op_error(out: dict) -> JSONResponse:
    code = out.get("error") or "internal"
    return _error(code, out.get("detail") or code, _status_for(code))


def _op_response(out):
    if isinstance(out, dict) and "error" in out:
        return _op_error(out)
    return JSONResponse(out)


def _raw(conn, sql: str, params=()) -> list[dict]:
    cursor = conn.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _degrees(conn, keys, namespace: str) -> dict[str, int]:
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


def _memory_json(row: dict, degree: int) -> dict:
    out = dict(row)
    out["degree"] = degree
    return out


def _memory_dict(row) -> dict:
    return {
        "key": row.key, "namespace": row.namespace, "value": row.value,
        "type": row.type, "stability": row.stability, "score": row.score,
        "uses": row.uses, "status": row.status, "agent_id": row.agent_id,
        "family_id": row.family_id, "relation_type": row.relation_type,
        "superseded_by": row.superseded_by, "created_at": row.created_at,
        "updated_at": row.updated_at, "valid_from": row.valid_from,
        "valid_to": row.valid_to, "valid_until": row.valid_until,
    }


def _int_param(params, name: str, default, low=None, high=None):
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise BadInput(f"{name} must be an integer")
    if low is not None and value < low:
        raise BadInput(f"{name} must be >= {low}")
    if high is not None and value > high:
        raise BadInput(f"{name} must be <= {high}")
    return value


def _names(blob: str | None) -> list[str]:
    return [name for name in (blob or "").split(",") if name]


def _fetch_memories(conn, keys, namespace: str) -> list[dict]:
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    rows = _raw(conn, f"SELECT {MEMORY_COLUMNS} FROM memories"
                      f" WHERE namespace=? AND key IN ({marks})",
                (namespace, *keys))
    order = {key: index for index, key in enumerate(keys)}
    return sorted(rows, key=lambda row: order[row["key"]])


def _search_items(conn, lock, query: str, namespace: str, kind, limit, status):
    hits = do_memory_search(conn, query, limit=limit, namespace=namespace,
                            type=kind, lock=lock)
    if isinstance(hits, dict):
        return hits
    rows = _fetch_memories(conn, [hit["key"] for hit in hits], namespace)
    if status:
        rows = [row for row in rows if row["status"] == status]
    degrees = _degrees(conn, [row["key"] for row in rows], namespace)
    items = [_memory_json(row, degrees.get(row["key"], 0)) for row in rows]
    return {"total": len(items), "limit": limit, "offset": 0, "items": items}


def _list_items(conn, namespace: str, kind, status, sort: str, limit, offset):
    where = ["namespace = ?"]
    params: list = [namespace]
    if kind:
        where.append("type = ?")
        params.append(kind)
    if status:
        where.append("status = ?")
        params.append(status)
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM memories WHERE {clause}",
                         params).fetchone()[0]
    rows = _raw(conn, f"SELECT {MEMORY_COLUMNS} FROM memories WHERE {clause}"
                      f" ORDER BY {sort} DESC, key ASC LIMIT ? OFFSET ?",
                (*params, limit, offset))
    degrees = _degrees(conn, [row["key"] for row in rows], namespace)
    items = [_memory_json(row, degrees.get(row["key"], 0)) for row in rows]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def _memory_detail(conn, key: str, namespace: str):
    with memo_db.orm_ctx(conn):
        row = Memory.get_or_none((Memory.key == key)
                                 & (Memory.namespace == namespace))
        if row is None:
            return None
        edge_rows = list(Edge.select().where(
            ((Edge.src_key == key) | (Edge.dst_key == key))
            & (Edge.namespace == namespace)))
        declarer_rows = list(EdgeDeclarer.select().where(
            ((EdgeDeclarer.src_key == key) | (EdgeDeclarer.dst_key == key))
            & (EdgeDeclarer.namespace == namespace)))
        alias_rows = list(Alias.select().where(
            ((Alias.segment == key) | (Alias.canonical == key))
            & (Alias.namespace == namespace)))
        family_rows = (list(Memory.select().where(
            (Memory.family_id == row.family_id)
            & (Memory.namespace == namespace)))
            if row.family_id else [])
    declared = {(d.src_key, d.dst_key, d.rel): d for d in declarer_rows}
    edges = []
    for edge in edge_rows:
        declarer = declared.get((edge.src_key, edge.dst_key, edge.rel))
        edges.append({
            "rel": edge.rel, "src_key": edge.src_key, "dst_key": edge.dst_key,
            "weight": edge.weight,
            "declarers": _names(declarer.declarers) if declarer else [],
            "status": declarer.status if declarer else "candidate",
            "direction": "out" if edge.src_key == key else "in"})
    subjects = {family_row.superseded_by: family_row.key
                for family_row in family_rows if family_row.superseded_by}
    family = [{
        "key": family_row.key, "status": family_row.status,
        "valid_from": family_row.valid_from, "valid_to": family_row.valid_to,
        "superseded_by": family_row.superseded_by,
        "relation_type": family_row.relation_type,
        "relation_to_subject": subjects.get(family_row.key)}
        for family_row in family_rows]
    aliases = [{
        "segment": alias.segment, "canonical": alias.canonical,
        "status": alias.status, "evidence": alias.evidence,
        "declarers": _names(alias.declarers)} for alias in alias_rows]
    declarers = [{
        "src_key": d.src_key, "dst_key": d.dst_key, "rel": d.rel,
        "declarers": _names(d.declarers), "status": d.status,
        "evidence": d.evidence} for d in declarer_rows]
    degrees = _degrees(conn, [key], namespace)
    related = _related(conn, key, row.embedding, namespace)
    return {"memory": _memory_json(_memory_dict(row), degrees.get(key, 0)),
            "edges": edges, "aliases": aliases, "family": family,
            "declarers": declarers, "related": related}


def _stats(conn, db_path: str, ui_built: bool) -> dict:
    def count(sql: str, params=()) -> int:
        return conn.execute(sql, params).fetchone()[0]

    counts = {
        "memories": count("SELECT COUNT(*) FROM memories"),
        "current": count("SELECT COUNT(*) FROM memories"
                         " WHERE status='current'"),
        "superseded": count("SELECT COUNT(*) FROM memories"
                            " WHERE status='superseded'"),
        "archived": count("SELECT COUNT(*) FROM archived_memories"),
        "skills": count("SELECT COUNT(*) FROM skills"
                        " WHERE source != 'tombstone'"),
        "agents": count("SELECT COUNT(*) FROM agents"),
        "edges": count("SELECT COUNT(*) FROM edges"),
        "aliases": count("SELECT COUNT(*) FROM aliases"),
        "episodes": count("SELECT COUNT(*) FROM episodes"),
    }
    by_type = {kind: 0 for kind in STABILITY}
    for kind, total in conn.execute(
            "SELECT type, COUNT(*) FROM memories GROUP BY type"):
        if kind in by_type:
            by_type[kind] = total
    by_source = {source: total for source, total in conn.execute(
        "SELECT source, COUNT(*) FROM skills WHERE source != 'tombstone'"
        " GROUP BY source")}
    namespaces = sorted({row[0] for row in conn.execute(
        "SELECT DISTINCT namespace FROM memories")}) or ["default"]
    last_activity = conn.execute(
        "SELECT MAX(updated_at) FROM memories").fetchone()[0]
    return {
        "version": VERSION, "vec": "vec" if memo_db.HAS_VEC else "bruteforce",
        "ui": ui_built, "db_path": db_path,
        "counts": counts,
        "by_type": by_type,
        "by_status": {"current": counts["current"],
                      "superseded": counts["superseded"]},
        "by_source": by_source, "namespaces": namespaces,
        "last_activity": last_activity or None,
    }


def _skills_index(conn, query, source, limit, offset):
    where = []
    params: list = []
    if source:
        where.append("source = ?")
        params.append(source)
    else:
        where.append("source != 'tombstone'")
    if query:
        where.append("(LOWER(name) LIKE ? OR LOWER(description) LIKE ?)")
        pattern = f"%{query.lower()}%"
        params.extend([pattern, pattern])
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM skills WHERE {clause}",
                         params).fetchone()[0]
    rows = _raw(conn, "SELECT name, description, body, hash, version, source,"
                      " created_at, updated_at FROM skills"
                      f" WHERE {clause} ORDER BY name ASC LIMIT ? OFFSET ?",
                (*params, limit, offset))
    items = []
    for row in rows:
        _, sections = split_revisions(row["body"])
        items.append({
            "name": row["name"], "description": row["description"],
            "version": row["version"], "source": row["source"],
            "hash": row["hash"], "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "body_bytes": len((row["body"] or "").encode("utf-8")),
            "revisions": len(sections) + 1})
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def _skill_detail(conn, lock, name: str, version):
    out = do_skill_get(conn, name, version, lock)
    if "error" in out:
        return out
    with memo_db.orm_ctx(conn):
        row = Skill.get_or_none(Skill.name == name)
    if row is not None:
        out["source"] = row.source
        out["created_at"] = row.created_at
        out["updated_at"] = row.updated_at
    return {"skill": out}


def _agents(conn) -> dict:
    with memo_db.orm_ctx(conn):
        agents = list(Agent.select().order_by(Agent.agent_id.asc()))
        states = list(AgentState.select().order_by(AgentState.skill.asc()))
    by_agent: dict[str, list] = {}
    for state in states:
        by_agent.setdefault(state.agent_id, []).append(state)
    items = []
    for agent in agents:
        skills = [{"skill": state.skill, "hash": state.hash,
                   "version": state.version, "updated_at": state.updated_at}
                  for state in by_agent.get(agent.agent_id, [])]
        try:
            capabilities = json.loads(agent.capabilities or "[]")
        except ValueError:
            capabilities = []
        items.append({
            "agent_id": agent.agent_id, "harness": agent.harness,
            "capabilities": capabilities, "first_seen": agent.first_seen,
            "last_seen": agent.last_seen, "skill_count": len(skills),
            "skills": skills})
    return {"items": items}


def _vectors(conn, namespace: str) -> list[tuple[str, bytes]]:
    rows = _raw(conn, "SELECT key, embedding FROM memories WHERE namespace = ?",
                (namespace,))
    return [(row["key"], bytes(row["embedding"])) for row in rows
            if row["embedding"]]


def _select_pairs(vectors, floor: float) -> dict[tuple[str, str], float]:
    candidates: dict[str, list] = {key: [] for key, _ in vectors}
    for index in range(len(vectors)):
        src_key, src_vec = vectors[index]
        for other in range(index + 1, len(vectors)):
            dst_key, dst_vec = vectors[other]
            score = embeddings.cosine(src_vec, dst_vec)
            if score >= floor:
                candidates[src_key].append((score, dst_key))
                candidates[dst_key].append((score, src_key))
    best: dict[tuple[str, str], float] = {}
    for src_key, scored in candidates.items():
        scored.sort(reverse=True)
        for score, dst_key in scored[:SIMILAR_TOP_K]:
            pair = (src_key, dst_key) if src_key <= dst_key else (dst_key, src_key)
            if score > best.get(pair, 0.0):
                best[pair] = score
    return best


def _compute_similar(conn, namespace: str) -> list[dict]:
    vectors = _vectors(conn, namespace)
    best = _select_pairs(vectors, SIMILAR_FLOOR)
    if not best:
        best = _select_pairs(vectors, SIMILAR_RETRY_FLOOR)
    pairs = [{"src_key": src, "dst_key": dst, "score": round(score, 3)}
             for (src, dst), score in best.items()]
    pairs.sort(key=lambda pair: (-pair["score"], pair["src_key"],
                                 pair["dst_key"]))
    return pairs[:SIMILAR_MAX]


def _similar(conn, namespace: str) -> list[dict]:
    stamp = conn.execute(
        "SELECT COUNT(*), MAX(updated_at) FROM memories WHERE namespace = ?",
        (namespace,)).fetchone()
    stamp = (stamp[0], stamp[1])
    now = time.monotonic()
    with _sim_lock:
        cached = _sim_cache.get(namespace)
        if cached is not None and cached[0] == stamp and cached[1] > now:
            return cached[2]
    started = time.monotonic()
    pairs = _compute_similar(conn, namespace)
    if time.monotonic() - started > SIMILAR_BUDGET:
        print("devWarn: similarity table build over 1.5s; returning none",
              file=sys.stderr)
        pairs = []
    with _sim_lock:
        _sim_cache[namespace] = (stamp, now + SIMILAR_TTL, pairs)
    return pairs


def _related(conn, key: str, embedding, namespace: str) -> list[dict]:
    if not embedding:
        return []
    embedding = bytes(embedding)
    scored = []
    for row in _raw(conn, "SELECT key, type, status, uses, embedding"
                          " FROM memories WHERE namespace = ?", (namespace,)):
        if row["key"] == key or not row["embedding"]:
            continue
        score = embeddings.cosine(embedding, bytes(row["embedding"]))
        if score < RELATED_FLOOR:
            continue
        scored.append({"key": row["key"], "type": row["type"],
                       "status": row["status"], "uses": row["uses"],
                       "score": round(score, 3)})
    scored.sort(key=lambda item: (-item["score"], item["key"]))
    return scored[:RELATED_TOP_K]


def _graph(conn, namespace: str, limit: int) -> dict:
    node_rows = _raw(conn, "SELECT key, type, status, uses, score FROM memories"
                           " WHERE namespace = ?", (namespace,))
    edge_rows = _raw(conn, "SELECT src_key, dst_key, rel, weight FROM edges"
                           " WHERE namespace = ?", (namespace,))
    degree: dict[str, int] = {}
    for edge in edge_rows:
        degree[edge["src_key"]] = degree.get(edge["src_key"], 0) + 1
        if edge["dst_key"] != edge["src_key"]:
            degree[edge["dst_key"]] = degree.get(edge["dst_key"], 0) + 1
    nodes = [{"key": row["key"], "type": row["type"], "status": row["status"],
              "uses": row["uses"], "score": row["score"],
              "degree": degree.get(row["key"], 0)} for row in node_rows]
    nodes.sort(key=lambda node: (-node["degree"], -node["uses"], node["key"]))
    nodes = nodes[:limit]
    keys = {node["key"] for node in nodes}
    edges = [{"src_key": edge["src_key"], "dst_key": edge["dst_key"],
              "rel": edge["rel"], "weight": edge["weight"]}
             for edge in edge_rows
             if edge["src_key"] in keys and edge["dst_key"] in keys]
    # The cached table covers the whole namespace; keep only pairs whose both
    # ends are in this (limit-truncated) node set.
    similar = [pair for pair in _similar(conn, namespace)
               if pair["src_key"] in keys and pair["dst_key"] in keys]
    return {"nodes": nodes, "edges": edges, "similar": similar}


def api_app(server):
    conn = server.memo_conn
    lock = server.memo_lock
    db_path = server.memo_db_path
    from memo.server.static_ui import ui_dir

    def guard(endpoint):
        async def wrapped(request):
            try:
                return await endpoint(request)
            except BadInput as exc:
                return _error("invalid_input", str(exc), 400)
            except Exception as exc:
                detail = internal_error(exc).get("detail", "internal error")
                return _error("internal", detail, 500)
        wrapped.__name__ = getattr(endpoint, "__name__", "handler")
        return wrapped

    async def _json_body(request) -> dict:
        try:
            data = await request.json()
        except Exception:
            raise BadInput("body must be valid JSON")
        if not isinstance(data, dict):
            raise BadInput("body must be a JSON object")
        return data

    async def stats(request):
        built = ui_dir() is not None
        return JSONResponse(await run_in_threadpool(_stats, conn, db_path,
                                                    built))

    async def health(request):
        return JSONResponse({"ok": True, "version": VERSION,
                             "vec": "vec" if memo_db.HAS_VEC else "bruteforce"})

    async def memories_list(request):
        params = request.query_params
        namespace = params.get("namespace") or "default"
        query = params.get("q")
        kind = params.get("type")
        status = params.get("status")
        limit = _int_param(params, "limit", 50, 1, 500)
        offset = _int_param(params, "offset", 0, 0)
        sort = params.get("sort") or "updated_at"
        if kind is not None and kind not in STABILITY:
            raise BadInput(f"type must be one of {sorted(STABILITY)}")
        if status is not None and status not in STATUSES:
            raise BadInput(f"status must be one of {list(STATUSES)}")
        if sort not in SORTS:
            raise BadInput(f"sort must be one of {list(SORTS)}")
        if query:
            out = await run_in_threadpool(_search_items, conn, lock, query,
                                          namespace, kind, limit, status)
        else:
            out = await run_in_threadpool(_list_items, conn, namespace, kind,
                                          status, sort, limit, offset)
        if isinstance(out, dict) and "error" in out:
            return _op_error(out)
        return JSONResponse(out)

    async def memory_detail(request):
        key = request.path_params["key"]
        namespace = request.query_params.get("namespace") or "default"
        out = await run_in_threadpool(_memory_detail, conn, key, namespace)
        if out is None:
            return _error("not_found", f"memory {key!r} not found", 404)
        return JSONResponse(out)

    async def memory_save(request):
        data = await _json_body(request)
        key = data.get("key")
        value = data.get("value")
        kind = data.get("type")
        if not isinstance(key, str) or not key:
            raise BadInput("key is required and must be a non-empty string")
        if not isinstance(value, str):
            raise BadInput("value is required and must be a string")
        if not isinstance(kind, str) or kind not in STABILITY:
            raise BadInput(f"type must be one of {sorted(STABILITY)}")
        links, aliases = data.get("links"), data.get("aliases")
        if links is not None and not isinstance(links, list):
            raise BadInput("links must be a list")
        if aliases is not None and not isinstance(aliases, list):
            raise BadInput("aliases must be a list")
        out = await run_in_threadpool(
            do_memory_save, conn, key, value, typ=kind,
            namespace=data.get("namespace") or "default",
            valid_from=data.get("valid_from"), lock=lock,
            valid_until=data.get("valid_until"), agent_id=data.get("agent_id"),
            links=links, aliases=aliases)
        return _op_response(out)

    async def memory_delete(request):
        key = request.path_params["key"]
        namespace = request.query_params.get("namespace") or "default"
        out = await run_in_threadpool(do_memory_delete, conn, key, namespace,
                                      lock)
        return _op_response(out)

    async def skills_list(request):
        params = request.query_params
        limit = _int_param(params, "limit", 200, 1, 500)
        offset = _int_param(params, "offset", 0, 0)
        out = await run_in_threadpool(_skills_index, conn, params.get("q"),
                                      params.get("source"), limit, offset)
        return JSONResponse(out)

    async def skill_detail(request):
        name = request.path_params["name"]
        version = _int_param(request.query_params, "version", None, 1)
        out = await run_in_threadpool(_skill_detail, conn, lock, name, version)
        if isinstance(out, dict) and "error" in out:
            return _op_error(out)
        return JSONResponse(out)

    async def skill_put(request):
        data = await _json_body(request)
        name = data.get("name")
        description = data.get("description")
        body = data.get("body")
        if not isinstance(name, str) or not name:
            raise BadInput("name is required and must be a non-empty string")
        if not isinstance(description, str):
            raise BadInput("description is required and must be a string")
        if not isinstance(body, str):
            raise BadInput("body is required and must be a string")
        out = await run_in_threadpool(
            do_skill_put, conn, name, description, body,
            source=data.get("source") or "local", lock=lock)
        return _op_response(out)

    async def skill_update(request):
        name = request.path_params["name"]
        data = await _json_body(request)
        patch = data.get("patch")
        if not isinstance(patch, str) or not patch:
            raise BadInput("patch is required and must be a non-empty string")
        out = await run_in_threadpool(do_skill_update, conn, name, patch, lock)
        return _op_response(out)

    async def agents(request):
        return JSONResponse(await run_in_threadpool(_agents, conn))

    async def graph(request):
        params = request.query_params
        namespace = params.get("namespace") or "default"
        limit = _int_param(params, "limit", 200, 1, 2000)
        return JSONResponse(await run_in_threadpool(_graph, conn, namespace,
                                                    limit))

    routes = [
        Route("/health", guard(health), methods=["GET"]),
        Route("/stats", guard(stats), methods=["GET"]),
        Route("/memories", guard(memories_list), methods=["GET"]),
        Route("/memories", guard(memory_save), methods=["POST"]),
        Route("/memories/{key}", guard(memory_detail), methods=["GET"]),
        Route("/memories/{key}", guard(memory_delete), methods=["DELETE"]),
        Route("/skills", guard(skills_list), methods=["GET"]),
        Route("/skills", guard(skill_put), methods=["POST"]),
        Route("/skills/{name}", guard(skill_detail), methods=["GET"]),
        Route("/skills/{name}/revisions", guard(skill_update),
              methods=["POST"]),
        Route("/agents", guard(agents), methods=["GET"]),
        Route("/graph", guard(graph), methods=["GET"]),
    ]
    return Starlette(routes=routes)
