from __future__ import annotations

from fastapi import Query
from starlette.concurrency import run_in_threadpool

from memo import db as memo_db
from memo.models.orm import (
    Alias as OrmAlias,
    Edge as OrmEdge,
    EdgeDeclarer as OrmEdgeDeclarer,
    Memory as OrmMemory,
)
from memo.server.operations import (
    do_memory_delete,
    do_memory_save,
    do_memory_search,
)

from .common import (
    MEMORY_COLUMNS,
    SORT_PATTERN,
    STATUS_PATTERN,
    TYPE_PATTERN,
    degrees,
    error,
    memory_dict,
    memory_json,
    names,
    op_error,
    op_response,
    raw,
)
from .graph import related
from .models import MemoryDetail, MemoryList, MemorySaveBody


def fetch_memories(conn, keys, namespace: str) -> list[dict]:
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    rows = raw(conn, f"SELECT {MEMORY_COLUMNS} FROM memories"
                  f" WHERE namespace=? AND key IN ({marks})",
               (namespace, *keys))
    order = {key: index for index, key in enumerate(keys)}
    return sorted(rows, key=lambda row: order[row["key"]])


def search_items(conn, lock, query: str, namespace: str, kind, limit, status):
    hits = do_memory_search(conn, query, limit=limit, namespace=namespace,
                            type=kind, lock=lock)
    if isinstance(hits, dict):
        return hits
    rows = fetch_memories(conn, [hit["key"] for hit in hits], namespace)
    if status:
        rows = [row for row in rows if row["status"] == status]
    degree_map = degrees(conn, [row["key"] for row in rows], namespace)
    items = [memory_json(row, degree_map.get(row["key"], 0)) for row in rows]
    return {"total": len(items), "limit": limit, "offset": 0, "items": items}


def list_items(conn, namespace: str, kind, status, sort: str, limit, offset):
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
    rows = raw(conn, f"SELECT {MEMORY_COLUMNS} FROM memories WHERE {clause}"
                    f" ORDER BY {sort} DESC, key ASC LIMIT ? OFFSET ?",
               (*params, limit, offset))
    degree_map = degrees(conn, [row["key"] for row in rows], namespace)
    items = [memory_json(row, degree_map.get(row["key"], 0)) for row in rows]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def build_memory_detail(conn, key: str, namespace: str):
    with memo_db.orm_ctx(conn):
        row = OrmMemory.get_or_none((OrmMemory.key == key)
                                    & (OrmMemory.namespace == namespace))
        if row is None:
            return None
        edge_rows = list(OrmEdge.select().where(
            ((OrmEdge.src_key == key) | (OrmEdge.dst_key == key))
            & (OrmEdge.namespace == namespace)))
        declarer_rows = list(OrmEdgeDeclarer.select().where(
            ((OrmEdgeDeclarer.src_key == key) | (OrmEdgeDeclarer.dst_key == key))
            & (OrmEdgeDeclarer.namespace == namespace)))
        alias_rows = list(OrmAlias.select().where(
            ((OrmAlias.segment == key) | (OrmAlias.canonical == key))
            & (OrmAlias.namespace == namespace)))
        family_rows = (list(OrmMemory.select().where(
            (OrmMemory.family_id == row.family_id)
            & (OrmMemory.namespace == namespace)))
            if row.family_id else [])
    declared = {(d.src_key, d.dst_key, d.rel): d for d in declarer_rows}
    edges = []
    for edge in edge_rows:
        declarer = declared.get((edge.src_key, edge.dst_key, edge.rel))
        edges.append({
            "rel": edge.rel, "src_key": edge.src_key, "dst_key": edge.dst_key,
            "weight": edge.weight,
            "declarers": names(declarer.declarers) if declarer else [],
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
        "declarers": names(alias.declarers)} for alias in alias_rows]
    declarers = [{
        "src_key": d.src_key, "dst_key": d.dst_key, "rel": d.rel,
        "declarers": names(d.declarers), "status": d.status,
        "evidence": d.evidence} for d in declarer_rows]
    degree_map = degrees(conn, [key], namespace)
    related_items = related(conn, key, row.embedding, namespace)
    return {"memory": memory_json(memory_dict(row), degree_map.get(key, 0)),
            "edges": edges, "aliases": aliases, "family": family,
            "declarers": declarers, "related": related_items}


def register(app, server) -> None:
    conn = server.memo_conn
    lock = server.memo_lock

    @app.get("/api/memories", response_model=MemoryList)
    async def memories_list(namespace: str | None = None, q: str | None = None,
                            type: str | None = Query(None, pattern=TYPE_PATTERN),
                            status: str | None = Query(None, pattern=STATUS_PATTERN),
                            limit: int = Query(50, ge=1, le=500),
                            offset: int = Query(0, ge=0),
                            sort: str = Query("updated_at", pattern=SORT_PATTERN)):
        namespace = namespace or "default"
        if q:
            out = await run_in_threadpool(search_items, conn, lock, q, namespace,
                                          type, limit, status)
        else:
            out = await run_in_threadpool(list_items, conn, namespace, type,
                                          status, sort, limit, offset)
        if isinstance(out, dict) and "error" in out:
            return op_error(out)
        return out

    @app.post("/api/memories")
    async def memory_save(body: MemorySaveBody):
        links = ([link.model_dump() for link in body.links]
                 if body.links is not None else None)
        aliases = ([alias.model_dump() for alias in body.aliases]
                   if body.aliases is not None else None)
        out = await run_in_threadpool(
            do_memory_save, conn, body.key, body.value, typ=body.type,
            namespace=body.namespace or "default", valid_from=body.valid_from,
            lock=lock, valid_until=body.valid_until, agent_id=body.agent_id,
            links=links, aliases=aliases)
        return op_response(out)

    @app.get("/api/memories/{key}", response_model=MemoryDetail)
    async def memory_detail(key: str, namespace: str | None = None):
        out = await run_in_threadpool(build_memory_detail, conn, key,
                                      namespace or "default")
        if out is None:
            return error("not_found", f"memory {key!r} not found", 404)
        return out

    @app.delete("/api/memories/{key}")
    async def memory_delete(key: str, namespace: str | None = None):
        out = await run_in_threadpool(do_memory_delete, conn, key,
                                      namespace or "default", lock)
        return op_response(out)
