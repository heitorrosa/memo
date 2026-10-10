"""Unified memo.db connect + migrate (spec §§5-6), Peewee-backed.

SQL text stays byte-identical to memo.models DDL (v1 design: keep the
strings, move execution onto Peewee). connect() still hands out the
raw sqlite3 connection (every call site + `with conn:` transaction
shape untouched) and binds the deferred Peewee database in orm.py to
the SAME file, so new code can use ORM queries + database.atomic().
Domain migrations (M-017) run raw SQL as today; playhouse.migrate's
SqliteMigrator is exposed for future structured ops.
"""

from __future__ import annotations

import sqlite3

from peewee import SqliteDatabase

from memo.models import render_schema
from memo.models import orm as orm_models
from memo.models.orm import database as peewee_database

MEMO_SCHEMA_VERSION = 3

# (domain, version, sql): a migration touches ONLY its own domain's tables (M-017).
MIGRATIONS: list[tuple[str, int, str]] = []

HAS_VEC = False
try:
    import sqlite_vec  # noqa: F401

    HAS_VEC = True
except ImportError:
    HAS_VEC = False

SCHEMA_TEXT = render_schema()
SEED_META = (
    ("schema_version", "3"),
    ("memory_schema_1", "applied"),
    ("skills_schema_1", "applied"),
)

PRAGMAS = {"journal_mode": "WAL", "foreign_keys": 1, "busy_timeout": 5000}


def peewee_init(path: str):
    """Bind the deferred ORM database to path (same file as connect)."""
    peewee_database.init(path, pragmas=PRAGMAS)
    return peewee_database


_db_cache: dict[str, SqliteDatabase] = {}


def orm_db(conn: sqlite3.Connection) -> SqliteDatabase:
    """Path-scoped Peewee database for conn's file.

    The deferred global follows the LAST connect() — wrong under
    simultaneous DBs (replay T-6 opens s2.db, later ORM reads on the
    first conn would hit s2.db). Resolving via PRAGMA database_list
    keeps every ORM query on conn's own file; same path shares one
    cached Database (WAL + busy_timeout make that safe).
    """
    try:
        path = conn.execute("PRAGMA database_list").fetchone()[2]
    except Exception:
        return peewee_database
    if not path:
        return peewee_database  # :memory: has no shared file; use global
    cached = _db_cache.get(path)
    if cached is None:
        cached = SqliteDatabase(path, pragmas=PRAGMAS)
        _db_cache[path] = cached
    return cached


def orm_ctx(conn: sqlite3.Connection):
    """bind_ctx for all ORM models onto conn's database."""
    return orm_db(conn).bind_ctx(orm_models.MODELS)


def atomic():
    """Peewee transaction context for new ORM call sites."""
    return peewee_database.atomic()


def migrator():
    """SqliteMigrator bound to the ORM database (structured migrations)."""
    from playhouse.migrate import SqliteMigrator

    return SqliteMigrator(peewee_database)


def probe_single_writer(conn: sqlite3.Connection) -> bool:
    """BEGIN IMMEDIATE probe: proves this process can take the write lock."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
        return True
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return False


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if HAS_VEC:
        try:
            import sqlite_vec

            sqlite_vec.load(conn)
        except Exception:
            pass
    peewee_init(path)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    with conn:
        conn.executescript(SCHEMA_TEXT)
        for key, value in SEED_META:
            conn.execute(
                "INSERT INTO memo_meta(k,v) VALUES(?,?) "
                "ON CONFLICT(k) DO NOTHING",
                (key, value),
            )
        for domain, version, sql in MIGRATIONS:
            key = f"{domain}_schema_{version}"
            if not conn.execute(
                "SELECT 1 FROM memo_meta WHERE k=?", (key,)
            ).fetchone():
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO memo_meta(k,v) VALUES(?,'applied')", (key,)
                )
        conn.execute("UPDATE memo_meta SET v='3' WHERE k='schema_version'")
    return MEMO_SCHEMA_VERSION
