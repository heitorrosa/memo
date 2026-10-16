from __future__ import annotations

import sqlite3

from peewee import SqliteDatabase
from playhouse.migrate import SqliteMigrator

from memo.models import orm as orm_models
from memo.models.orm import render_schema as _render
from memo.models.orm import database as peewee_database

MEMO_SCHEMA_VERSION = 3


def create_agents(migrator) -> None:
    models = [orm_models.Agent, orm_models.AgentState]
    with migrator.database.bind_ctx(models):
        migrator.database.create_tables(models, safe=True)


def ensure_memory_columns(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM memo_meta WHERE k='memories_schema_2'"
    ).fetchone():
        return
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    with conn:
        for table in ("memories", "archived_memories"):
            if table not in tables:
                continue
            cols = {row[1] for row in
                    conn.execute(f"PRAGMA table_info({table})")}
            if "valid_until" not in cols:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN valid_until TEXT")
            if "agent_id" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN agent_id TEXT")
        conn.execute(
            "INSERT INTO memo_meta(k,v) VALUES('memories_schema_2','applied')")


def ensure_edges(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM memo_meta WHERE k='memories_schema_3'"
    ).fetchone():
        return
    peewee_db = orm_db(conn)
    with peewee_db.bind_ctx([orm_models.Edge]):
        peewee_db.create_tables([orm_models.Edge], safe=True)
    with conn:
        conn.execute(
            "INSERT INTO memo_meta(k,v) VALUES('memories_schema_3','applied')")


def ensure_aliases(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM memo_meta WHERE k='memories_schema_4'"
    ).fetchone():
        return
    peewee_db = orm_db(conn)
    models = [orm_models.Alias, orm_models.EdgeDeclarer]
    with peewee_db.bind_ctx(models):
        peewee_db.create_tables(models, safe=True)
    with conn:
        conn.execute(
            "INSERT INTO memo_meta(k,v) VALUES('memories_schema_4','applied')")


MIGRATIONS: list = [("agents", 1, create_agents)]

HAS_VEC = False
try:
    import sqlite_vec

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
    peewee_database.init(path, pragmas=PRAGMAS)
    return peewee_database


_db_cache: dict[str, SqliteDatabase] = {}


def orm_db(conn: sqlite3.Connection) -> SqliteDatabase:
    try:
        path = conn.execute("PRAGMA database_list").fetchone()[2]
    except Exception:
        return peewee_database
    if not path:
        return peewee_database
    cached = _db_cache.get(path)
    if cached is None:
        cached = SqliteDatabase(path, pragmas=PRAGMAS)
        _db_cache[path] = cached
    return cached


def orm_ctx(conn: sqlite3.Connection):
    return orm_db(conn).bind_ctx(orm_models.MODELS)


def atomic():
    return peewee_database.atomic()


def rebuild_fts(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table'"
        " AND name='memories_fts'"
    ).fetchone():
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")


def probe_single_writer(conn: sqlite3.Connection) -> bool:
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
    try:
        path = conn.execute("PRAGMA database_list").fetchone()[2]
    except Exception:
        path = ""
    if path:
        peewee_db = orm_db(conn)
        with peewee_db.bind_ctx(orm_models.CREATE_MODELS):
            peewee_db.create_tables(orm_models.CREATE_MODELS, safe=True)
        ensure_memory_columns(conn)
        ensure_edges(conn)
        ensure_aliases(conn)
    else:
        with conn:
            conn.executescript(_render())
            ensure_memory_columns(conn)
            ensure_edges(conn)
            ensure_aliases(conn)
            rebuild_fts(conn)
            return seed(conn)
    with conn:
        conn.executescript(orm_models.RAW_TAIL)
        rebuild_fts(conn)
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
