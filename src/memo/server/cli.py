"""CLI: argparse, health probe, backup, stdio/HTTP entry points."""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys

import httpx
import uvicorn

from memo import db
from memo.server.core import build_server, dump_tool_names
from memo.server.http import build_http_app
from memo.server.shared import resolve_domains, startup_line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memo")
    parser.add_argument("--db", default=None)
    parser.add_argument("--dump-tools", action="store_true")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--health", nargs="?", const="http://127.0.0.1:8000/health",
                        default=None)
    parser.add_argument("--backup", default=None)
    return parser


def refuse_if_locked(path: str) -> bool:
    """Best-effort single-writer guard (M-032): refuse a DB another process
    holds a write lock on, and point at the container instead."""
    if path == ":memory:":
        return False
    try:
        probe = sqlite3.connect(path, timeout=1.0)  # raw: lock probe is connection-level, ORM has no equivalent
        try:
            probe.execute("BEGIN IMMEDIATE")  # raw: write-lock probe, ORM cannot express
            probe.execute("ROLLBACK")
            return False
        except sqlite3.OperationalError:
            return True
        finally:
            probe.close()
    except sqlite3.Error:
        return False


def health_probe(url: str) -> int:
    try:
        response = httpx.get(url, timeout=10.0)
        healthy = response.status_code == 200 and response.json().get("ok") is True
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"memo health FAIL {url}: {exc}", file=sys.stderr)
        return 1
    print(f"memo health {'OK' if healthy else 'FAIL'} {url}")
    return 0 if healthy else 1


def backup_database(db_path: str, destination: str) -> int:
    # raw: sqlite3 backup API is file-level, not CRUD — ORM cannot cover it.
    source_conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        destination_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(destination_conn)
        finally:
            destination_conn.close()
    finally:
        source_conn.close()
    print(f"memo backup {db_path} -> {destination}")
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
    uvicorn.run(build_http_app(server, token), host=args.host, port=args.port,
                log_level="warning")
    return 0
