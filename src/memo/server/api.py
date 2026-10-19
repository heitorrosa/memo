from __future__ import annotations

import json
import sys
import threading
import time

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from memo import db as memo_db
from memo import embeddings
from memo.memory.common import STABILITY
from memo.models.orm import (
    Agent as OrmAgent,
    AgentState as OrmAgentState,
    Alias as OrmAlias,
    Edge as OrmEdge,
    EdgeDeclarer as OrmEdgeDeclarer,
    Memory as OrmMemory,
    Skill as OrmSkill,
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
TYPE_PATTERN = "^(" + "|".join(STABILITY) + ")$"
STATUS_PATTERN = "^(" + "|".join(STATUSES) + ")$"
SORT_PATTERN = "^(" + "|".join(SORTS) + ")$"

SIMILAR_TOP_K = 3
SIMILAR_FLOOR = 0.35
SIMILAR_RETRY_FLOOR = 0.25
SIMILAR_MAX = 400
SIMILAR_TTL = 30.0
SIMILAR_BUDGET = 1.5
RELATED_TOP_K = 5
RELATED_FLOOR = 0.25

sim_cache: dict[str, tuple] = {}
sim_lock = threading.Lock()


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


def build_stats(conn, db_path: str, ui_built: bool) -> dict:
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


def skills_index(conn, query, source, limit, offset):
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
    rows = raw(conn, "SELECT name, description, body, hash, version, source,"
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


def build_skill_detail(conn, lock, name: str, version):
    out = do_skill_get(conn, name, version, lock)
    if "error" in out:
        return out
    with memo_db.orm_ctx(conn):
        row = OrmSkill.get_or_none(OrmSkill.name == name)
    if row is not None:
        out["source"] = row.source
        out["created_at"] = row.created_at
        out["updated_at"] = row.updated_at
    return {"skill": out}


def build_agents(conn) -> dict:
    with memo_db.orm_ctx(conn):
        agent_rows = list(OrmAgent.select().order_by(OrmAgent.agent_id.asc()))
        states = list(OrmAgentState.select().order_by(OrmAgentState.skill.asc()))
    by_agent: dict[str, list] = {}
    for state in states:
        by_agent.setdefault(state.agent_id, []).append(state)
    items = []
    for agent in agent_rows:
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


def vectors(conn, namespace: str) -> list[tuple[str, bytes]]:
    rows = raw(conn, "SELECT key, embedding FROM memories WHERE namespace = ?",
               (namespace,))
    return [(row["key"], bytes(row["embedding"])) for row in rows
            if row["embedding"]]


def select_pairs(vectors, floor: float) -> dict[tuple[str, str], float]:
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


def compute_similar(conn, namespace: str) -> list[dict]:
    vec_rows = vectors(conn, namespace)
    best = select_pairs(vec_rows, SIMILAR_FLOOR)
    if not best:
        best = select_pairs(vec_rows, SIMILAR_RETRY_FLOOR)
    pairs = [{"src_key": src, "dst_key": dst, "score": round(score, 3)}
             for (src, dst), score in best.items()]
    pairs.sort(key=lambda pair: (-pair["score"], pair["src_key"],
                                 pair["dst_key"]))
    return pairs[:SIMILAR_MAX]


def similar(conn, namespace: str) -> list[dict]:
    stamp = conn.execute(
        "SELECT COUNT(*), MAX(updated_at) FROM memories WHERE namespace = ?",
        (namespace,)).fetchone()
    stamp = (stamp[0], stamp[1])
    now = time.monotonic()
    with sim_lock:
        cached = sim_cache.get(namespace)
        if cached is not None and cached[0] == stamp and cached[1] > now:
            return cached[2]
    started = time.monotonic()
    pairs = compute_similar(conn, namespace)
    if time.monotonic() - started > SIMILAR_BUDGET:
        print("devWarn: similarity table build over 1.5s; returning none",
              file=sys.stderr)
        pairs = []
    with sim_lock:
        sim_cache[namespace] = (stamp, now + SIMILAR_TTL, pairs)
    return pairs


def related(conn, key: str, embedding, namespace: str) -> list[dict]:
    if not embedding:
        return []
    embedding = bytes(embedding)
    scored = []
    for row in raw(conn, "SELECT key, type, status, uses, embedding"
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


def build_graph(conn, namespace: str, limit: int) -> dict:
    node_rows = raw(conn, "SELECT key, type, status, uses, score FROM memories"
                           " WHERE namespace = ?", (namespace,))
    edge_rows = raw(conn, "SELECT src_key, dst_key, rel, weight FROM edges"
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
    similar_pairs = [pair for pair in similar(conn, namespace)
                     if pair["src_key"] in keys and pair["dst_key"] in keys]
    return {"nodes": nodes, "edges": edges, "similar": similar_pairs}


class MemoryLink(BaseModel):
    to: str
    rel: str


class MemoryAlias(BaseModel):
    segment: str
    canonical: str


class MemorySaveBody(BaseModel):
    key: str = Field(min_length=1)
    value: str
    type: str
    namespace: str = "default"
    links: list[MemoryLink] | None = None
    aliases: list[MemoryAlias] | None = None
    agent_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str) -> str:
        if value not in STABILITY:
            raise ValueError(f"type must be one of {sorted(STABILITY)}")
        return value


class SkillPutBody(BaseModel):
    name: str = Field(min_length=1)
    description: str
    body: str
    source: str = "local"


class SkillRevisionBody(BaseModel):
    patch: str = Field(min_length=1)


class Counts(BaseModel):
    memories: int
    current: int
    superseded: int
    archived: int
    skills: int
    agents: int
    edges: int
    aliases: int
    episodes: int


class Stats(BaseModel):
    version: int
    vec: str
    ui: bool
    db_path: str
    counts: Counts
    by_type: dict[str, int]
    by_status: dict[str, int]
    by_source: dict[str, int]
    namespaces: list[str]
    last_activity: str | None


class Health(BaseModel):
    ok: bool
    version: int
    vec: str


class Memory(BaseModel):
    key: str
    namespace: str
    value: str
    type: str
    stability: float
    score: float
    uses: int
    status: str
    agent_id: str | None
    family_id: str | None
    relation_type: str | None
    superseded_by: str | None
    created_at: str
    updated_at: str
    valid_from: str | None
    valid_to: str | None
    valid_until: str | None
    degree: int


class MemoryList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[Memory]


class MemoryEdge(BaseModel):
    rel: str
    src_key: str
    dst_key: str
    weight: float
    declarers: list[str]
    status: str
    direction: str


class Alias(BaseModel):
    segment: str
    canonical: str
    status: str
    evidence: int | str | None
    declarers: list[str]


class FamilyRow(BaseModel):
    key: str
    status: str
    valid_from: str | None
    valid_to: str | None
    superseded_by: str | None
    relation_type: str | None
    relation_to_subject: str | None


class Declarer(BaseModel):
    src_key: str
    dst_key: str
    rel: str
    declarers: list[str]
    status: str
    evidence: int | str | None


class RelatedMemory(BaseModel):
    key: str
    type: str
    status: str
    uses: int
    score: float


class MemoryDetail(BaseModel):
    memory: Memory
    edges: list[MemoryEdge]
    aliases: list[Alias]
    family: list[FamilyRow]
    declarers: list[Declarer]
    related: list[RelatedMemory]


class SkillSummary(BaseModel):
    name: str
    description: str
    version: int
    source: str
    hash: str
    created_at: str
    updated_at: str
    body_bytes: int
    revisions: int


class SkillList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[SkillSummary]


class Skill(BaseModel):
    name: str
    description: str
    body: str
    hash: str
    version: int
    source: str
    created_at: str
    updated_at: str


class SkillDetail(BaseModel):
    skill: Skill


class AgentSkillRef(BaseModel):
    skill: str
    hash: str
    version: int
    updated_at: str


class Agent(BaseModel):
    agent_id: str
    harness: str
    capabilities: list[str]
    first_seen: str
    last_seen: str
    skill_count: int
    skills: list[AgentSkillRef]


class AgentList(BaseModel):
    items: list[Agent]


class GraphNode(BaseModel):
    key: str
    type: str
    status: str
    uses: int
    score: float
    degree: int


class GraphEdge(BaseModel):
    src_key: str
    dst_key: str
    rel: str
    weight: float


class GraphLink(BaseModel):
    src_key: str
    dst_key: str
    score: float


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    similar: list[GraphLink]


def create_api_app(server, lifespan=None) -> FastAPI:
    app = FastAPI(title="memo", version="3.0.0", docs_url="/api/docs",
                   openapi_url="/api/openapi.json", redoc_url=None,
                   swagger_ui_oauth2_redirect_url=None, lifespan=lifespan)
    conn = server.memo_conn
    lock = server.memo_lock
    db_path = server.memo_db_path

    @app.exception_handler(RequestValidationError)
    async def validation_failed(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        roots = {str(err["loc"][0]) for err in errors if err["loc"]}
        status = 400 if roots and roots <= {"query", "path"} else 422
        message = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in errors)
        return error("invalid_input", message, status)

    @app.exception_handler(Exception)
    async def internal_failed(request: Request, exc: Exception):
        detail = internal_error(exc).get("detail", "internal error")
        return error("internal", detail, 500)

    @app.get("/api/health", response_model=Health)
    async def health():
        return {"ok": True, "version": VERSION,
                "vec": "vec" if memo_db.HAS_VEC else "bruteforce"}

    @app.get("/api/stats", response_model=Stats)
    async def stats():
        from memo.server.static_ui import ui_dir

        built = ui_dir() is not None
        return await run_in_threadpool(build_stats, conn, db_path, built)

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

    @app.get("/api/skills", response_model=SkillList)
    async def skills_list(q: str | None = None, source: str | None = None,
                          limit: int = Query(200, ge=1, le=500),
                          offset: int = Query(0, ge=0)):
        return await run_in_threadpool(skills_index, conn, q, source, limit,
                                       offset)

    @app.post("/api/skills")
    async def skill_put(body: SkillPutBody):
        out = await run_in_threadpool(
            do_skill_put, conn, body.name, body.description, body.body,
            source=body.source or "local", lock=lock)
        return op_response(out)

    @app.get("/api/skills/{name}", response_model=SkillDetail)
    async def skill_detail(name: str, version: int | None = Query(None, ge=1)):
        out = await run_in_threadpool(build_skill_detail, conn, lock, name,
                                      version)
        if isinstance(out, dict) and "error" in out:
            return op_error(out)
        return out

    @app.post("/api/skills/{name}/revisions")
    async def skill_update(name: str, body: SkillRevisionBody):
        out = await run_in_threadpool(do_skill_update, conn, name, body.patch,
                                      lock)
        return op_response(out)

    @app.get("/api/agents", response_model=AgentList)
    async def agents():
        return await run_in_threadpool(build_agents, conn)

    @app.get("/api/graph", response_model=Graph)
    async def graph(namespace: str | None = None,
                    limit: int = Query(200, ge=1, le=2000)):
        return await run_in_threadpool(build_graph, conn, namespace or "default",
                                       limit)

    return app
