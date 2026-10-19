from __future__ import annotations

from fastapi import Query
from starlette.concurrency import run_in_threadpool

from memo import db as memo_db
from memo.models.orm import Skill as OrmSkill
from memo.server.operations import do_skill_get, do_skill_put, do_skill_update
from memo.skills.crud import split_revisions

from .common import op_error, op_response, raw
from .models import SkillDetail, SkillList, SkillPutBody, SkillRevisionBody


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


def register(app, server) -> None:
    conn = server.memo_conn
    lock = server.memo_lock

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
