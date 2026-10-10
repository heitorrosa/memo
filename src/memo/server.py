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


def _descriptions() -> dict:
    """Tool/prompt descriptions (§§14/8/21 — they ARE the control plane).

    Loaded once from tool_descriptions.json; wording is byte-identical
    to the former DESC_* literals.
    """
    path = pathlib.Path(__file__).with_name("tool_descriptions.json")
    return json.loads(path.read_text(encoding="utf-8"))


DESCRIPTIONS = _descriptions()

MEMORY_TOOLS = ("memory_search", "memory_save", "memory_delete")
SKILL_TOOLS = ("skill_get", "skill_put", "skill_update", "skill_install",
               "skill_sync")
ALL_TOOLS = ("memory_delete", "memory_save", "memory_search") + SKILL_TOOLS


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def resolve_domains() -> tuple[bool, bool]:
    """(memory_on, skills_on) from MEMO_DISABLE_* (spec §4.1b)."""
    return not _flag("MEMO_DISABLE_MEMORY"), not _flag("MEMO_DISABLE_SKILLS")


def startup_line(db_path: str, has_vec: bool, mem_on: bool,
                 skills_on: bool) -> str:
    return (f"memo v3 db={db_path} vec={'vec' if has_vec else 'bruteforce'}"
            f" memory={'on' if mem_on else 'off'}"
            f" skills={'on' if skills_on else 'off'}")


def _log(op: str, ns: str, ms: float, rows: int, action: str) -> None:
    # NEVER values/bodies/keys/bundle text (keys can be PII-shaped).
    print(f"op={op} ns={ns} ms={ms:.1f} rows={rows} action={action}",
          file=sys.stderr)


def _internal(exc: Exception) -> dict:
    first = str(exc).splitlines()[0] if str(exc) else ""
    detail = f"{type(exc).__name__}: {first}".rstrip(": ")
    return errors.err("internal", detail)


# --- pure tool impls (catch-all -> internal envelope, never raise) ---

def do_memory_search(conn, query, limit=10, namespace="default",
                     valid_at="now", as_known_at=None,
                     include_superseded=False, lock=None):
    t = time.perf_counter()
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
        return _internal(exc)
    rows = len(out) if isinstance(out, list) else 0
    _log("memory_search", namespace, (time.perf_counter() - t) * 1000, rows,
         out.get("error", "-") if isinstance(out, dict) else "-")
    return out


def do_memory_save(conn, key, value, typ="context", namespace="default",
                   valid_from=None, lock=None):
    t = time.perf_counter()
    try:
        if lock is not None:
            with lock:
                out = memory.save(conn, key, value, type=typ,
                                  namespace=namespace, valid_from=valid_from)
        else:
            out = memory.save(conn, key, value, type=typ, namespace=namespace,
                              valid_from=valid_from)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return _internal(exc)
    _log("memory_save", namespace, (time.perf_counter() - t) * 1000, 1,
         out.get("action", out.get("error", "-")))
    return out


def do_memory_delete(conn, key, namespace="default", lock=None):
    t = time.perf_counter()
    try:
        if lock is not None:
            with lock:
                out = memory.remove(conn, key, namespace=namespace)
        else:
            out = memory.remove(conn, key, namespace=namespace)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return _internal(exc)
    _log("memory_delete", namespace, (time.perf_counter() - t) * 1000, 1,
         out.get("action", "-"))
    return out


def _sync_call(conn, harness, known, filt, offset, limit, lock):
    if lock is not None:
        with lock:
            return skills.sync(conn, harness, known=known, filter=filt,
                               offset=offset, limit=limit)
    return skills.sync(conn, harness, known=known, filter=filt,
                       offset=offset, limit=limit)


def do_skill_sync(conn, harness, known=None, filt=None, offset=0, limit=None,
                  lock=None):
    t = time.perf_counter()
    try:
        out = _sync_call(conn, harness, known, filt, offset, limit, lock)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return _internal(exc)
    rows = len(out.get("files_to_write", [])) if isinstance(out, dict) else 0
    _log("skill_sync", "-", (time.perf_counter() - t) * 1000, rows,
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
        return _internal(exc)


def do_skill_put(conn, name, description, body, source="local", lock=None):
    try:
        if lock is not None:
            with lock:
                return skills.put(conn, name, description, body, source=source)
        return skills.put(conn, name, description, body, source=source)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return _internal(exc)


def do_skill_update(conn, name, patch, lock=None):
    try:
        if lock is not None:
            with lock:
                return skills.update(conn, name, patch)
        return skills.update(conn, name, patch)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return _internal(exc)


def do_skill_install(conn, repo, skill_name, fetch=None, lock=None):
    try:
        kw = {} if fetch is None else {"fetch": fetch}
        if lock is not None:
            with lock:
                return skills.install(conn, repo, skill_name, **kw)
        return skills.install(conn, repo, skill_name, **kw)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return _internal(exc)


# --- bootstrap text (flag-aware) ---

def stats_line(conn, mem_on=True, skills_on=True) -> str:
    n = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE status='current'").fetchone()[0]
    k = conn.execute(
        "SELECT COUNT(*) FROM skills WHERE source != 'tombstone'").fetchone()[0]
    mem = f"{n}/250 memories" if mem_on else "memories DISABLED"
    skl = f"{k} skills" if skills_on else "skills DISABLED"
    return f"Stats: {mem} · {skl} · db v3"


def bootstrap_text(conn, mem_on=True, skills_on=True) -> str:
    if mem_on and skills_on:
        return summary.bootstrap_text(conn)
    parts = ["# memo bootstrap — durable memory + skill index "
             "(loaded once per conversation; do not re-fetch what you hold)",
             "## Orientation", summary.ORIENTATION]
    if mem_on:
        mem = summary.top_memories_lines(conn)
        parts += [f"## Top memories ({len(mem)})",
                  *(mem or ["(none yet)"])]
    if skills_on:
        l0 = skills.l0_index(conn)
        parts += [f"## Skill index ({len(l0)} — full, keep in mind for the"
                  f" whole conversation)", *(l0 or ["(no skills yet)"])]
    parts.append(f"## {stats_line(conn, mem_on, skills_on)}")
    return "\n".join(parts)


# --- server build ---

def _server_connect(path: str) -> sqlite3.Connection:
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
        env_mem, env_sk = resolve_domains()
        memory_on = env_mem if memory_on is None else memory_on
        skills_on = env_sk if skills_on is None else skills_on
    if not memory_on and not skills_on:
        print("memo: no domains enabled", file=sys.stderr)
        raise SystemExit(2)
    conn = _server_connect(db_path)
    lock = threading.Lock()
    srv = FastMCP("memo")
    srv.memo_conn, srv.memo_lock = conn, lock  # noqa: ATTR-defined
    srv.memo_db_path, srv.memo_mem_on, srv.memo_skills_on = (  # noqa: ATTR-defined
        db_path, memory_on, skills_on)

    if memory_on:
        @srv.tool(name="memory_search", description=DESCRIPTIONS["memory_search"])
        def memory_search(query: str, limit: int = 10,
                          namespace: str = "default", valid_at: str = "now",
                          as_known_at: str | None = None,
                          include_superseded: bool = False) -> list | dict:
            return do_memory_search(conn, query, limit, namespace, valid_at,
                                    as_known_at, include_superseded, lock)

        @srv.tool(name="memory_save", description=DESCRIPTIONS["memory_save"])
        def memory_save(key: str, value: str, type: str = "context",
                        namespace: str = "default",
                        valid_from: str | None = None) -> dict:
            return do_memory_save(conn, key, value, type, namespace,
                                  valid_from, lock)

        @srv.tool(name="memory_delete", description=DESCRIPTIONS["memory_delete"])
        def memory_delete(key: str,
                          namespace: str = "default") -> dict:
            return do_memory_delete(conn, key, namespace, lock)

    if skills_on:
        @srv.tool(name="skill_sync", description=DESCRIPTIONS["skill_sync"])
        def skill_sync(harness: str, known: list | None = None,
                       filter: str | None = None, offset: int = 0,
                       limit: int | None = None) -> dict:
            return do_skill_sync(conn, harness, known, filter, offset,
                                 limit, lock)

        @srv.tool(name="skill_get", description=DESCRIPTIONS["skill_get"])
        def skill_get(name: str, version: int | None = None) -> dict:
            return do_skill_get(conn, name, version, lock)

        @srv.tool(name="skill_put", description=DESCRIPTIONS["skill_put"])
        def skill_put(name: str, description: str, body: str,
                      source: str = "local") -> dict:
            return do_skill_put(conn, name, description, body, source, lock)

        @srv.tool(name="skill_update", description=DESCRIPTIONS["skill_update"])
        def skill_update(name: str, patch: str) -> dict:
            return do_skill_update(conn, name, patch, lock)

        @srv.tool(name="skill_install", description=DESCRIPTIONS["skill_install"])
        def skill_install(repo: str, skill_name: str) -> dict:
            return do_skill_install(conn, repo, skill_name, lock=lock)

    @srv.prompt(name="bootstrap", description=DESCRIPTIONS["bootstrap"])
    def bootstrap() -> str:
        with lock:
            return bootstrap_text(conn, memory_on, skills_on)

    @srv.resource("memo://summary", description=DESCRIPTIONS["bootstrap"])
    def memo_summary() -> str:
        with lock:
            return bootstrap_text(conn, memory_on, skills_on)

    return srv


def dump_tool_names(srv) -> list[str]:
    import asyncio

    return sorted(t.name for t in asyncio.run(srv.list_tools()))


# --- HTTP (PRIMARY): bearer gate + /healthz ---

class _Bearer:
    def __init__(self, app, token: str):
        self.app, self._token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            await self.app(scope, receive, send)
            return
        got = ""
        for k, v in scope.get("headers", []):
            if k == b"authorization":
                got = v.decode("latin-1")
                break
        want = f"Bearer {self._token}"
        try:
            ok = hmac.compare_digest(got, want)
        except Exception:
            ok = False
        if not ok:
            body = b'{"error":"unauthorized"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body))
                                     .encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_http_app(srv, token: str):
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    async def healthz(request):
        return JSONResponse({"ok": True, "version": VERSION,
                             "vec": "vec" if db.HAS_VEC else "bruteforce"})

    mcp_app = srv.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        # Mount does not propagate lifespan: run the inner MCP app's
        # lifespan (session_manager.run task group) inside ours, else
        # every /mcp request 500s with "Task group is not initialized".
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = Starlette(lifespan=lifespan,
                    routes=[Route("/healthz", healthz, methods=["GET"]),
                            Mount("/", app=mcp_app)])
    return _Bearer(app, token)


# --- CLI ---

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memo")
    p.add_argument("--db", default=None)
    p.add_argument("--dump-tools", action="store_true")
    p.add_argument("--http", action="store_true")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--health", nargs="?", const="http://127.0.0.1:8000/healthz",
                   default=None)
    p.add_argument("--backup", default=None)
    return p


def _refuse_if_locked(path: str) -> bool:
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


def _health_probe(url: str) -> int:
    import httpx

    try:
        r = httpx.get(url, timeout=10.0)
        ok = r.status_code == 200 and r.json().get("ok") is True
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"memo health FAIL {url}: {exc}", file=sys.stderr)
        return 1
    print(f"memo health {'OK' if ok else 'FAIL'} {url}")
    return 0 if ok else 1


def _backup(db_path: str, dest: str) -> int:
    src = sqlite3.connect(db_path, timeout=10.0)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    print(f"memo backup {db_path} -> {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.health is not None and not args.http and args.db is None \
            and args.backup is None and not args.dump_tools:
        return _health_probe(args.health)
    if not args.db:
        print("memo: --db <path> is required", file=sys.stderr)
        return 2
    if args.backup:
        return _backup(args.db, args.backup)
    mem_on, sk_on = resolve_domains()
    if not mem_on and not sk_on:
        print("memo: no domains enabled", file=sys.stderr)
        return 2
    if args.dump_tools:
        print("\n".join(dump_tool_names(build_server(args.db))))
        return 0
    if _refuse_if_locked(args.db):
        print(f"memo: {args.db} is held by another process (the container"
              f" is the sole writer) — refusing stdio over the live DB",
              file=sys.stderr)
        return 2
    if args.http:
        return main_http(["--db", args.db, "--port", str(args.port),
                          "--host", args.host])
    srv = build_server(args.db, mem_on, sk_on)
    print(startup_line(args.db, db.HAS_VEC, mem_on, sk_on), file=sys.stderr)
    asyncio.run(srv.run_stdio_async())
    return 0


def main_http(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    token = os.getenv("MEMO_TOKEN", "")
    if not token:
        print("memo: MEMO_TOKEN required for --http", file=sys.stderr)
        return 2  # M-033
    if not args.db:
        print("memo: --db <path> is required", file=sys.stderr)
        return 2
    mem_on, sk_on = resolve_domains()
    if not mem_on and not sk_on:
        print("memo: no domains enabled", file=sys.stderr)
        return 2
    if _refuse_if_locked(args.db):
        print(f"memo: {args.db} is held by another process — refusing"
              f" a second writer on the live DB", file=sys.stderr)
        return 2
    srv = build_server(args.db, mem_on, sk_on)
    print(startup_line(args.db, db.HAS_VEC, mem_on, sk_on), file=sys.stderr)
    import uvicorn

    uvicorn.run(build_http_app(srv, token), host=args.host, port=args.port,
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
