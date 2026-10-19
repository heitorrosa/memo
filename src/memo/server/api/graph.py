from __future__ import annotations

import sys
import threading
import time

from fastapi import Query
from starlette.concurrency import run_in_threadpool

from memo import embeddings

from .common import raw
from .models import Graph

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


def register(app, server) -> None:
    conn = server.memo_conn

    @app.get("/api/graph", response_model=Graph)
    async def graph(namespace: str | None = None,
                    limit: int = Query(200, ge=1, le=2000)):
        return await run_in_threadpool(build_graph, conn, namespace or "default",
                                       limit)
