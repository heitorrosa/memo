"""Drift guard (Task 5): committed schema.sql is generated from memo.models,
plus ORM models match the migrated schema (peewee lane)."""
import pathlib

import memo.models
from memo.models.orm import render_schema


def test_committed_schema_matches_generated():
    generated = render_schema()
    assert generated.startswith("-- GENERATED from memo.models")
    committed_path = pathlib.Path(memo.models.__file__).parent.with_name("schema.sql")
    assert committed_path.read_text(encoding="utf-8") == generated


def test_orm_models_match_migrated_schema(tmp_path):
    """Model-vs-migrated-schema: every ORM model exposes exactly the columns
    migrate() creates (name sets; nullability lives in DDL, not Python)."""
    from memo import db
    from memo.models import orm

    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    try:
        def columns(table):
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

        assert {f.column_name for f in orm.MemoMeta._meta.sorted_fields} == columns("memo_meta")
        assert {f.column_name for f in orm.Memory._meta.sorted_fields} == columns("memories")
        assert {f.column_name for f in orm.ArchivedMemory._meta.sorted_fields} == columns("archived_memories")
        assert {f.column_name for f in orm.Episode._meta.sorted_fields} == columns("episodes")
        assert {f.column_name for f in orm.Skill._meta.sorted_fields} == columns("skills")
        assert {f.column_name for f in orm.Agent._meta.sorted_fields} == columns("agents")
        assert {f.column_name for f in orm.AgentState._meta.sorted_fields} == columns("agent_state")
        assert orm.MemoryFTS._meta.options == {"content": "memories", "content_rowid": "rowid"}
        fts_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='memories_fts'").fetchone()[0]
        # sqlite normalizes content='memories' to content=memories in storage.
        assert "content=memories" in fts_sql.replace("'", "") and "content_rowid=rowid" in fts_sql
        indexes = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        # Peewee-canonical index names (auto-generated table_col1_col2;
        # pre-migration idx_memories_* names persist only on old DB files).
        assert {"memory_family_id_valid_from", "memory_status_namespace"} <= indexes
    finally:
        conn.close()
