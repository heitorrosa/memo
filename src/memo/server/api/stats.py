from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from memo import db as memo_db
from memo.memory.common import STABILITY
from memo.server.shared import VERSION

from .models import Health, Stats


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


def register(app, server) -> None:
    conn = server.memo_conn
    db_path = server.memo_db_path

    @app.get("/api/health", response_model=Health)
    async def health():
        return {"ok": True, "version": VERSION,
                "vec": "vec" if memo_db.HAS_VEC else "bruteforce"}

    @app.get("/api/stats", response_model=Stats)
    async def stats():
        from memo.server.static_ui import ui_dir

        built = ui_dir() is not None
        return await run_in_threadpool(build_stats, conn, db_path, built)
