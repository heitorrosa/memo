"""Unified memo.db connect + migrate (spec §§5-6), Peewee-canonical.

Models ARE the schema: migrate() drives database.create_tables +
the RAW_TAIL triggers/clone. No DDL strings exist as source of truth;
render_schema() (derived from the same Models) serves only the
schema.sql artifact and the :memory: fallback below.
Structural alterations go through playhouse.migrate SqliteMigrator ops
recorded under {domain}_schema_{version} keys — NO alembic, NO SQLAlchemy.
v3→v3 needs no ops: the Model schema is equivalent to the old text form,
and migrate is idempotent (safe create + IF NOT EXISTS tail).
"""

from __future__ import annotations

import sqlite3

from peewee import SqliteDatabase
from playhouse.migrate import SqliteMigrator

from memo.models import orm as orm_models
from memo.models.orm import render_schema as _render
from memo.models.orm import database as peewee_database

MEMO_SCHEMA_VERSION = 3


def create_agents(migrator) -> None:
    """Create the agents/agent_state tables through the migrator's database.

    playhouse.migrate has no CREATE TABLE op, so the structural change runs
    via migrator.database.create_tables (safe, idempotent) — the recorded
    `agents_schema_1` key is what marks the domain migration applied.
    """
    models = [orm_models.Agent, orm_models.AgentState]
    with migrator.database.bind_ctx(models):
        migrator.database.create_tables(models, safe=True)


# (domain, version, op): op(migrator) applies structural alterations via
# playhouse.migrate (recorded under f"{domain}_schema_{version}").
MIGRATIONS: list = [("agents", 1, create_agents)]

HAS_VEC = False
try:
    import sqlite_vec  # noqa: F401

    HAS_VEC = True
except ImportError:
    HAS_VEC = False

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
        path = conn.execute("PRAGMA database_list").fetchone()[2]  # raw: resolve conn's file for ORM binding; ORM cannot report its own file
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
    return SqliteMigrator(peewee_database)


def probe_single_writer(conn: sqlite3.Connection) -> bool:
    """BEGIN IMMEDIATE probe: proves this process can take the write lock."""
    # raw: lock probe is connection-level — ORM has no equivalent.
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
    # raw: connection setup + PRAGMAs; peewee_init binds the ORM to SAME file.
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
    # raw: DDL application — tables via create_tables, triggers/clone via
    # RAW_TAIL (ORM-inexpressible); the :memory: branch has no shared file
    # so it applies the same Models-derived text on conn itself.
    try:
        path = conn.execute("PRAGMA database_list").fetchone()[2]
    except Exception:
        path = ""
    if path:
        peewee_db = orm_db(conn)
        with peewee_db.bind_ctx(orm_models.CREATE_MODELS):
            peewee_db.create_tables(orm_models.CREATE_MODELS, safe=True)
    else:
        with conn:
            conn.executescript(_render())
            return seed(conn)
    with conn:
        conn.executescript(orm_models.RAW_TAIL)
        return seed(conn)


def seed(conn: sqlite3.Connection) -> int:
    for key, value in SEED_META:
        conn.execute(
            "INSERT INTO memo_meta(k,v) VALUES(?,?) "
            "ON CONFLICT(k) DO NOTHING",
            (key, value),
        )
    if MIGRATIONS:
        try:
            path = conn.execute("PRAGMA database_list").fetchone()[2]
        except Exception:
            path = ""
        migrator = SqliteMigrator(orm_db(conn) if path else peewee_database)
        for domain, version, op in MIGRATIONS:
            key = f"{domain}_schema_{version}"
            if not conn.execute(
                "SELECT 1 FROM memo_meta WHERE k=?", (key,)
            ).fetchone():
                op(migrator)
                conn.execute(
                    "INSERT INTO memo_meta(k,v) VALUES(?,'applied')", (key,)
                )
    conn.execute("UPDATE memo_meta SET v='3' WHERE k='schema_version'")
    return MEMO_SCHEMA_VERSION
