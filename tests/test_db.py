"""Task 1 tests: unified memo.db schema + migrations + FTS triggers (spec §§5-6)."""

from memo import db


def _mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def test_unified_schema(tmp_path):
    conn = _mem(tmp_path)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')"
        )
    }
    for t in (
        "memo_meta",
        "memories",
        "memories_fts",
        "archived_memories",
        "episodes",
        "skills",
    ):
        assert t in tables, f"missing table {t}"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
    for c in (
        "key",
        "namespace",
        "value",
        "type",
        "embedding",
        "stability",
        "score",
        "uses",
        "created_at",
        "updated_at",
        "valid_from",
        "valid_to",
        "status",
        "family_id",
        "superseded_by",
        "relation_type",
    ):
        assert c in cols, f"memories missing column {c}"
    arch = {r[1] for r in conn.execute("PRAGMA table_info(archived_memories)")}
    assert arch == cols | {"archived_at", "archived_reason"}
    assert (
        conn.execute("SELECT v FROM memo_meta WHERE k='schema_version'").fetchone()[0]
        == "3"
    )
    # Second migrate is a no-op returning the same version.
    assert db.migrate(conn) == 3
    assert db.MEMO_SCHEMA_VERSION == 3
    conn.close()


def test_fts_triggers(tmp_path):
    conn = _mem(tmp_path)
    conn.execute(
        "INSERT INTO memories(key,value,type,embedding,stability,created_at,"
        "updated_at,valid_from,family_id) VALUES"
        "('editor.indent','prefers tabs','preference',zeroblob(1536),14.0,"
        "'2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00',"
        "'2026-10-01T00:00:00+00:00','fam1')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'tabs'"
    ).fetchone()[0] == 1
    conn.execute("UPDATE memories SET value='prefers spaces' WHERE key='editor.indent'")
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'spaces'"
    ).fetchone()[0] == 1
    conn.execute("DELETE FROM memories WHERE key='editor.indent'")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM memories_fts").fetchone()[0] == 0
    conn.close()
    assert isinstance(db.MIGRATIONS, list)
