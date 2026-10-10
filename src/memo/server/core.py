"""Server build: connection, FastMCP tool registration, tool listing."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading

from mcp.server.fastmcp import FastMCP

from memo import db
from memo.server.bootstrap import bootstrap_text
from memo.server.descriptions import DESCRIPTIONS
from memo.server.operations import (
    do_memory_delete,
    do_memory_save,
    do_memory_search,
    do_skill_get,
    do_skill_install,
    do_skill_put,
    do_skill_sync,
    do_skill_update,
)
from memo.server.shared import resolve_domains


def server_connect(path: str) -> sqlite3.Connection:
    # Same shape as db.connect + check_same_thread=False: the HTTP layer
    # serves tools from worker threads behind one lock (single writer).
    # raw: connection setup + PRAGMAs — ORM binds to this file after.
    conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if db.HAS_VEC:
        try:
            import sqlite_vec

            sqlite_vec.load(conn)
        except Exception:
            pass
    db.peewee_init(path)
    if not db.probe_single_writer(conn):
        print("memo: write lock busy at startup, continuing", file=sys.stderr)
    db.migrate(conn)
    return conn


def build_server(db_path: str, memory_on: bool | None = None,
                 skills_on: bool | None = None):
    if memory_on is None or skills_on is None:
        env_memory, env_skills = resolve_domains()
        memory_on = env_memory if memory_on is None else memory_on
        skills_on = env_skills if skills_on is None else skills_on
    if not memory_on and not skills_on:
        print("memo: no domains enabled", file=sys.stderr)
        raise SystemExit(2)
    conn = server_connect(db_path)
    lock = threading.Lock()
    server = FastMCP("memo")
    server.memo_conn, server.memo_lock = conn, lock  # noqa: ATTR-defined
    server.memo_db_path, server.memo_mem_on, server.memo_skills_on = (  # noqa: ATTR-defined
        db_path, memory_on, skills_on)

    if memory_on:
        @server.tool(name="memory_search", description=DESCRIPTIONS["memory_search"])
        def memory_search(query: str, limit: int = 10,
                          namespace: str = "default", valid_at: str = "now",
                          as_known_at: str | None = None,
                          include_superseded: bool = False) -> list | dict:
            return do_memory_search(conn, query, limit, namespace, valid_at,
                                    as_known_at, include_superseded, lock)

        @server.tool(name="memory_save", description=DESCRIPTIONS["memory_save"])
        def memory_save(key: str, value: str, type: str = "context",
                        namespace: str = "default",
                        valid_from: str | None = None) -> dict:
            return do_memory_save(conn, key, value, type, namespace,
                                  valid_from, lock)

        @server.tool(name="memory_delete", description=DESCRIPTIONS["memory_delete"])
        def memory_delete(key: str,
                          namespace: str = "default") -> dict:
            return do_memory_delete(conn, key, namespace, lock)

    if skills_on:
        @server.tool(name="skill_sync", description=DESCRIPTIONS["skill_sync"])
        def skill_sync(harness: str, known: list | None = None,
                       filter: str | None = None, offset: int = 0,
                       limit: int | None = None) -> dict:
            return do_skill_sync(conn, harness, known, filter, offset,
                                 limit, lock)

        @server.tool(name="skill_get", description=DESCRIPTIONS["skill_get"])
        def skill_get(name: str, version: int | None = None) -> dict:
            return do_skill_get(conn, name, version, lock)

        @server.tool(name="skill_put", description=DESCRIPTIONS["skill_put"])
        def skill_put(name: str, description: str, body: str,
                      source: str = "local") -> dict:
            return do_skill_put(conn, name, description, body, source, lock)

        @server.tool(name="skill_update", description=DESCRIPTIONS["skill_update"])
        def skill_update(name: str, patch: str) -> dict:
            return do_skill_update(conn, name, patch, lock)

        @server.tool(name="skill_install", description=DESCRIPTIONS["skill_install"])
        def skill_install(repo: str, skill_name: str) -> dict:
            return do_skill_install(conn, repo, skill_name, lock=lock)

    @server.prompt(name="bootstrap", description=DESCRIPTIONS["bootstrap"])
    def bootstrap() -> str:
        with lock:
            return bootstrap_text(conn, memory_on, skills_on)

    @server.resource("memo://summary", description=DESCRIPTIONS["bootstrap"])
    def memo_summary() -> str:
        with lock:
            return bootstrap_text(conn, memory_on, skills_on)

    return server


def dump_tool_names(server) -> list[str]:
    return sorted(tool.name for tool in asyncio.run(server.list_tools()))
