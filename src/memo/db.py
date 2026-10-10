"""Unified memo.db connect + migrate (spec §§5-6)."""

from __future__ import annotations

import pathlib
import sqlite3

MEMO_SCHEMA_VERSION = 3

# (domain, version, sql): a migration touches ONLY its own domain's tables (M-017).
MIGRATIONS: list[tuple[str, int, str]] = []

HAS_VEC = False
try:
    import sqlite_vec  # noqa: F401

    HAS_VEC = True
except ImportError:
    HAS_VEC = False

SCHEMA_TEXT = pathlib.Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
SEED_META = (
    ("schema_version", "3"),
    ("memory_schema_1", "applied"),
    ("skills_schema_1", "applied"),
)


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
