"""Single memo MCP server (spec §§4,7,24): 8 tools + bootstrap + memo://summary.

Primary transport: Streamable HTTP (container). Stdio is a fallback.
Import rule: server -> {memory, skills, summary, maintenance} — the ONLY
cross-domain join here is bootstrap text (read-only).
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import pathlib
import sqlite3
import sys
import threading
import time

from memo import db, errors, memory, skills, summary

VERSION = 3


def load_descriptions() -> dict:
    """Tool/prompt descriptions (§§14/8/21 — they ARE the control plane).

    Loaded once from tool_descriptions.json; wording is byte-identical
    to the former DESC_* literals.
    """
    path = pathlib.Path(__file__).with_name("tool_descriptions.json")
    return json.loads(path.read_text(encoding="utf-8"))


DESCRIPTIONS = load_descriptions()

MEMORY_TOOLS = ("memory_search", "memory_save", "memory_delete")
SKILL_TOOLS = ("skill_get", "skill_put", "skill_update", "skill_install",
               "skill_sync")
ALL_TOOLS = ("memory_delete", "memory_save", "memory_search") + SKILL_TOOLS


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def resolve_domains() -> tuple[bool, bool]:
    """(memory_on, skills_on) from MEMO_DISABLE_* (spec §4.1b)."""
    return not env_flag("MEMO_DISABLE_MEMORY"), not env_flag("MEMO_DISABLE_SKILLS")


def startup_line(db_path: str, has_vec: bool, memory_on: bool,
                 skills_on: bool) -> str:
    return (f"memo v3 db={db_path} vec={'vec' if has_vec else 'bruteforce'}"
            f" memory={'on' if memory_on else 'off'}"
            f" skills={'on' if skills_on else 'off'}")


def log_operation(operation: str, namespace: str, elapsed_ms: float, row_count: int, action: str) -> None:
    # NEVER values/bodies/keys/bundle text (keys can be PII-shaped).
    print(f"op={operation} ns={namespace} ms={elapsed_ms:.1f} rows={row_count} action={action}",
          file=sys.stderr)


def internal_error(exc: Exception) -> dict:
    first = str(exc).splitlines()[0] if str(exc) else ""
    detail = f"{type(exc).__name__}: {first}".rstrip(": ")
    return errors.err("internal", detail)


# --- pure tool impls (catch-all -> internal envelope, never raise) ---

def do_memory_search(conn, query, limit=10, namespace="default",
                     valid_at="now", as_known_at=None,
                     include_superseded=False, lock=None):
    start = time.perf_counter()
    try:
        if lock is not None:
            with lock:
                out = memory.search(conn, query, limit=limit,
                                    namespace=namespace, valid_at=valid_at,
                                    as_known_at=as_known_at,
                                    include_superseded=include_superseded)
        else:
            out = memory.search(conn, query, limit=limit, namespace=namespace,
                                valid_at=valid_at, as_known_at=as_known_at,
                                include_superseded=include_superseded)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)
    row_count = len(out) if isinstance(out, list) else 0
    log_operation("memory_search", namespace, (time.perf_counter() - start) * 1000, row_count,
         out.get("error", "-") if isinstance(out, dict) else "-")
    return out


def do_memory_save(conn, key, value, typ="context", namespace="default",
                   valid_from=None, lock=None):
    start = time.perf_counter()
    try:
        if lock is not None:
            with lock:
                out = memory.save(conn, key, value, type=typ,
                                  namespace=namespace, valid_from=valid_from)
        else:
            out = memory.save(conn, key, value, type=typ, namespace=namespace,
                              valid_from=valid_from)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)
    log_operation("memory_save", namespace, (time.perf_counter() - start) * 1000, 1,
         out.get("action", out.get("error", "-")))
    return out


def do_memory_delete(conn, key, namespace="default", lock=None):
    start = time.perf_counter()
    try:
        if lock is not None:
            with lock:
                out = memory.remove(conn, key, namespace=namespace)
        else:
            out = memory.remove(conn, key, namespace=namespace)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)
    log_operation("memory_delete", namespace, (time.perf_counter() - start) * 1000, 1,
         out.get("action", "-"))
    return out


def sync_call_locked(conn, harness, known, filt, offset, limit, lock):
    if lock is not None:
        with lock:
            return skills.sync(conn, harness, known=known, filter=filt,
                               offset=offset, limit=limit)
    return skills.sync(conn, harness, known=known, filter=filt,
                       offset=offset, limit=limit)


def do_skill_sync(conn, harness, known=None, filt=None, offset=0, limit=None,
                  lock=None):
    start = time.perf_counter()
    try:
        out = sync_call_locked(conn, harness, known, filt, offset, limit, lock)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)
    row_count = len(out.get("files_to_write", [])) if isinstance(out, dict) else 0
    log_operation("skill_sync", "-", (time.perf_counter() - start) * 1000, row_count,
         out.get("mode", out.get("error", "-")) if isinstance(out, dict)
         else "-")
    return out


def do_skill_get(conn, name, version=None, lock=None):
    try:
        if lock is not None:
            with lock:
                return skills.get(conn, name, version=version)
        return skills.get(conn, name, version=version)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)


def do_skill_put(conn, name, description, body, source="local", lock=None):
    try:
        if lock is not None:
            with lock:
                return skills.put(conn, name, description, body, source=source)
        return skills.put(conn, name, description, body, source=source)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)


def do_skill_update(conn, name, patch, lock=None):
    try:
        if lock is not None:
            with lock:
                return skills.update(conn, name, patch)
        return skills.update(conn, name, patch)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)


def do_skill_install(conn, repository, skill_name, fetch=None, lock=None):
    try:
        fetch_kwargs = {} if fetch is None else {"fetch": fetch}
        if lock is not None:
            with lock:
                return skills.install(conn, repository, skill_name, **fetch_kwargs)
        return skills.install(conn, repository, skill_name, **fetch_kwargs)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)


# --- bootstrap text (flag-aware) ---

def stats_line(conn, memory_on=True, skills_on=True) -> str:
    memory_count = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE status='current'").fetchone()[0]
    skill_count = conn.execute(
        "SELECT COUNT(*) FROM skills WHERE source != 'tombstone'").fetchone()[0]
    memory_text = f"{memory_count}/250 memories" if memory_on else "memories DISABLED"
    skills_text = f"{skill_count} skills" if skills_on else "skills DISABLED"
    return f"Stats: {memory_text} · {skills_text} · db v3"


def bootstrap_text(conn, memory_on=True, skills_on=True) -> str:
    if memory_on and skills_on:
        return summary.bootstrap_text(conn)
    parts = ["# memo bootstrap — durable memory + skill index "
             "(loaded once per conversation; do not re-fetch what you hold)",
             "## Orientation", summary.ORIENTATION]
    if memory_on:
        memory_lines = summary.top_memories_lines(conn)
        parts += [f"## Top memories ({len(memory_lines)})",
                  *(memory_lines or ["(none yet)"])]
    if skills_on:
        skill_index = skills.l0_index(conn)
        parts += [f"## Skill index ({len(skill_index)} — full, keep in mind for the"
                  f" whole conversation)", *(skill_index or ["(no skills yet)"])]
    parts.append(f"## {stats_line(conn, memory_on, skills_on)}")
    return "\n".join(parts)


# --- server build ---

def server_connect(path: str) -> sqlite3.Connection:
    # Same shape as db.connect + check_same_thread=False: the HTTP layer
    # serves tools from worker threads behind one lock (single writer).
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
    db.migrate(conn)
    return conn


def build_server(db_path: str, memory_on: bool | None = None,
                 skills_on: bool | None = None):
    from mcp.server.fastmcp import FastMCP

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
    import asyncio

    return sorted(tool.name for tool in asyncio.run(server.list_tools()))


# --- HTTP (PRIMARY): bearer gate + /healthz ---

class BearerAuth:
    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            await self.app(scope, receive, send)
            return
        presented = ""
        for header_key, header_value in scope.get("headers", []):
            if header_key == b"authorization":
                presented = header_value.decode("latin-1")
                break
        expected = f"Bearer {self.token}"
        try:
            authorized = hmac.compare_digest(presented, expected)
        except Exception:
            authorized = False
        if not authorized:
            body = b'{"error":"unauthorized"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body))
                                     .encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_http_app(server, token: str):
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    async def health_check(request):
        return JSONResponse({"ok": True, "version": VERSION,
                             "vec": "vec" if db.HAS_VEC else "bruteforce"})

    mcp_app = server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        # Mount does not propagate lifespan: run the inner MCP app's
        # lifespan (session_manager.run task group) inside ours, else
        # every /mcp request 500s with "Task group is not initialized".
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = Starlette(lifespan=lifespan,
                    routes=[Route("/healthz", health_check, methods=["GET"]),
                            Mount("/", app=mcp_app)])
    return BearerAuth(app, token)


# --- CLI ---

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memo")
    parser.add_argument("--db", default=None)
    parser.add_argument("--dump-tools", action="store_true")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--health", nargs="?", const="http://127.0.0.1:8000/healthz",
                   default=None)
    parser.add_argument("--backup", default=None)
    return parser


def refuse_if_locked(path: str) -> bool:
    """Best-effort single-writer guard (M-032): refuse a DB another process
    holds a write lock on, and point at the container instead."""
    if path == ":memory:":
        return False
    try:
        probe = sqlite3.connect(path, timeout=1.0)
        try:
            probe.execute("BEGIN IMMEDIATE")
            probe.execute("ROLLBACK")
            return False
        except sqlite3.OperationalError:
            return True
        finally:
            probe.close()
    except sqlite3.Error:
        return False


def health_probe(url: str) -> int:
    import httpx

    try:
        response = httpx.get(url, timeout=10.0)
        healthy = response.status_code == 200 and response.json().get("ok") is True
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"memo health FAIL {url}: {exc}", file=sys.stderr)
        return 1
    print(f"memo health {'OK' if healthy else 'FAIL'} {url}")
    return 0 if healthy else 1


def backup_database(db_path: str, destination: str) -> int:
    source_conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        destination_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(destination_conn)
        finally:
            destination_conn.close()
    finally:
        source_conn.close()
    print(f"memo backup {db_path} -> {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.health is not None and not args.http and args.db is None \
            and args.backup is None and not args.dump_tools:
        return health_probe(args.health)
    if not args.db:
        print("memo: --db <path> is required", file=sys.stderr)
        return 2
    if args.backup:
        return backup_database(args.db, args.backup)
    memory_on, skills_on = resolve_domains()
    if not memory_on and not skills_on:
        print("memo: no domains enabled", file=sys.stderr)
        return 2
    if args.dump_tools:
        print("\n".join(dump_tool_names(build_server(args.db))))
        return 0
    if refuse_if_locked(args.db):
        print(f"memo: {args.db} is held by another process (the container"
              f" is the sole writer) — refusing stdio over the live DB",
              file=sys.stderr)
        return 2
    if args.http:
        return main_http(["--db", args.db, "--port", str(args.port),
                          "--host", args.host])
    server = build_server(args.db, memory_on, skills_on)
    print(startup_line(args.db, db.HAS_VEC, memory_on, skills_on), file=sys.stderr)
    asyncio.run(server.run_stdio_async())
    return 0


def main_http(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.getenv("MEMO_TOKEN", "")
    if not token:
        print("memo: MEMO_TOKEN required for --http", file=sys.stderr)
        return 2  # M-033
    if not args.db:
        print("memo: --db <path> is required", file=sys.stderr)
        return 2
    memory_on, skills_on = resolve_domains()
    if not memory_on and not skills_on:
        print("memo: no domains enabled", file=sys.stderr)
        return 2
    if refuse_if_locked(args.db):
        print(f"memo: {args.db} is held by another process — refusing"
              f" a second writer on the live DB", file=sys.stderr)
        return 2
    server = build_server(args.db, memory_on, skills_on)
    print(startup_line(args.db, db.HAS_VEC, memory_on, skills_on), file=sys.stderr)
    import uvicorn

    uvicorn.run(build_http_app(server, token), host=args.host, port=args.port,
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
