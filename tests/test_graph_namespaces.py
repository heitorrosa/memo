import sqlite3

import pytest

from memo import db, embeddings, temporal
from memo.memory.graph import neighborhood
from memo.memory.save import save
from memo.memory.search import search

FROZEN_NOW = "2026-10-16T12:00:00-03:00"
AGENT_A, AGENT_B = "A" * 8, "B" * 8

OLD_SCHEMA = """
CREATE TABLE memo_meta (k TEXT NOT NULL PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE edges (src_key TEXT NOT NULL, dst_key TEXT NOT NULL, rel TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0, created_at TEXT NOT NULL,
  PRIMARY KEY (src_key, dst_key, rel),
  CHECK (rel IN ('mentions','supports','contradicts','derived_from')));
CREATE TABLE aliases (segment TEXT NOT NULL, canonical TEXT NOT NULL,
  declarers TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'candidate',
  evidence INTEGER NOT NULL DEFAULT 1, first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL, PRIMARY KEY (segment, canonical),
  CHECK (status IN ('candidate','confirmed')));
CREATE TABLE edge_declarers (src_key TEXT NOT NULL, dst_key TEXT NOT NULL,
  rel TEXT NOT NULL, declarers TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'candidate', evidence INTEGER NOT NULL DEFAULT 1,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  PRIMARY KEY (src_key, dst_key, rel),
  CHECK (status IN ('candidate','confirmed')));
INSERT INTO edges VALUES('a','b','supports',0.4,'2026-01-01');
INSERT INTO aliases VALUES('s','c','AAAAAAAA','candidate',1,'2026-01-01','2026-01-01');
INSERT INTO edge_declarers VALUES('a','b','supports','AAAAAAAA','candidate',1,
  '2026-01-01','2026-01-01');
INSERT INTO memo_meta VALUES('schema_version','3');
INSERT INTO memo_meta VALUES('memory_schema_1','applied');
INSERT INTO memo_meta VALUES('skills_schema_1','applied');
INSERT INTO memo_meta VALUES('memories_schema_3','applied');
INSERT INTO memo_meta VALUES('memories_schema_4','applied');
"""


@pytest.fixture(autouse=True)
def fallback():
    embeddings.force_fallback = True
    try:
        yield
    finally:
        embeddings.force_fallback = False


@pytest.fixture(autouse=True)
def frozen_time(monkeypatch):
    monkeypatch.setattr(temporal, "now_iso", lambda: FROZEN_NOW)


def mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def edges(conn, namespace):
    return conn.execute(
        "SELECT src_key, dst_key, rel FROM edges WHERE namespace=?"
        " ORDER BY src_key, dst_key, rel", (namespace,)).fetchall()


def test_links_stay_in_their_namespace(tmp_path):
    conn = mem(tmp_path)
    for ns in ("nsA", "nsB"):
        save(conn, "graph.alpha", "durable atlas note alpha", namespace=ns)
    save(conn, "graph.beta", "durable atlas note beta", namespace="nsA",
         links=[{"to": "graph.alpha", "rel": "supports"}], agent_id=AGENT_A)
    save(conn, "graph.beta", "durable atlas note beta", namespace="nsB",
         agent_id=AGENT_A)
    assert ("graph.beta", "graph.alpha", "supports") in edges(conn, "nsA")
    assert ("graph.beta", "graph.alpha", "supports") not in edges(conn, "nsB")
    conn.close()


def test_neighborhood_does_not_cross_namespaces(tmp_path):
    conn = mem(tmp_path)
    save(conn, "graph.alpha", "durable atlas note alpha", namespace="nsA")
    save(conn, "graph.beta", "durable atlas note beta", namespace="nsA",
         links=[{"to": "graph.alpha", "rel": "supports"}], agent_id=AGENT_A)
    save(conn, "graph.beta", "durable atlas note beta", namespace="nsB")
    a = {r["key"] for r in neighborhood(conn, "graph.beta", 1, namespace="nsA")}
    b = {r["key"] for r in neighborhood(conn, "graph.beta", 1, namespace="nsB")}
    assert "graph.alpha" in a
    assert "graph.alpha" not in b
    conn.close()


def test_alias_agreement_scoped_by_namespace(tmp_path):
    conn = mem(tmp_path)
    for ns, agent in (("nsA", AGENT_A), ("nsB", AGENT_B)):
        save(conn, "seg.one", "durable atlas note for segment one", namespace=ns,
             aliases=[{"segment": "seg.one", "canonical": "canon.one"}],
             agent_id=agent)
    rows = conn.execute(
        "SELECT namespace, declarers, status, evidence FROM aliases"
        " ORDER BY namespace").fetchall()
    assert rows == [("nsA", "AAAAAAAA", "candidate", 1),
                    ("nsB", "BBBBBBBB", "candidate", 1)]
    conn.close()


def test_edge_declarers_scoped_by_namespace(tmp_path):
    conn = mem(tmp_path)
    for ns in ("nsA", "nsB"):
        save(conn, "graph.alpha", "durable atlas note alpha", namespace=ns)
        save(conn, "graph.beta", "durable atlas note beta", namespace=ns,
             links=[{"to": "graph.alpha", "rel": "supports"}], agent_id=AGENT_A)
    rows = conn.execute(
        "SELECT namespace, declarers, status FROM edge_declarers"
        " ORDER BY namespace").fetchall()
    assert rows == [("nsA", "AAAAAAAA", "candidate"),
                    ("nsB", "AAAAAAAA", "candidate")]
    assert conn.execute(
        "SELECT count(*) FROM edges WHERE rel='supports'").fetchone()[0] == 2
    conn.close()


def test_weight_upgrade_scoped_by_namespace(tmp_path):
    conn = mem(tmp_path)
    for ns in ("nsA", "nsB"):
        save(conn, "graph.alpha", "durable atlas note alpha", namespace=ns)
        save(conn, "graph.beta", "durable atlas note beta", namespace=ns,
             links=[{"to": "graph.alpha", "rel": "supports"}], agent_id=AGENT_A)
    save(conn, "graph.beta", "durable atlas note beta", namespace="nsA",
         links=[{"to": "graph.alpha", "rel": "supports"}], agent_id=AGENT_B)

    def weight(ns):
        return conn.execute(
            "SELECT weight FROM edges WHERE namespace=? AND src_key='graph.beta'"
            " AND dst_key='graph.alpha'", (ns,)).fetchone()[0]

    assert weight("nsA") == 0.8
    assert weight("nsB") == 0.4
    conn.close()


def test_contradiction_flags_scoped_by_namespace(tmp_path):
    conn = mem(tmp_path)
    save(conn, "theme.dark.choice", "also display dark theme", namespace="nsA")
    save(conn, "glare.mode.setting", "also display light theme", namespace="nsA",
         links=[{"to": "theme.dark.choice", "rel": "contradicts"}],
         agent_id=AGENT_A)
    save(conn, "theme.dark.choice", "also display dark theme", namespace="nsB")
    save(conn, "glare.mode.setting", "also display light theme", namespace="nsB")
    hits_a = {h["key"]: h for h in search(conn, "display", namespace="nsA")}
    hits_b = {h["key"]: h for h in search(conn, "display", namespace="nsB")}
    assert hits_a["glare.mode.setting"]["contradictions"] == [
        {"key": "theme.dark.choice", "declarers": [AGENT_A], "weight": 0.0}]
    assert all("contradictions" not in h for h in hits_b.values())
    conn.close()


def test_link_target_must_exist_in_same_namespace(tmp_path):
    conn = mem(tmp_path)
    save(conn, "graph.alpha", "durable atlas note alpha", namespace="nsA")
    r = save(conn, "graph.beta", "durable atlas note beta", namespace="nsB",
             links=[{"to": "graph.alpha", "rel": "supports"}], agent_id=AGENT_A)
    assert r["action"] == "created"
    assert "does not exist" in r["suggestion"]
    assert conn.execute(
        "SELECT count(*) FROM edges WHERE namespace='nsB' AND rel='supports'"
    ).fetchone()[0] == 0
    conn.close()


def test_same_key_saves_are_independent_across_namespaces(tmp_path):
    conn = mem(tmp_path)
    save(conn, "shared.note", "durable shared note for nsA", namespace="nsA")
    save(conn, "shared.note", "durable shared note for nsB", namespace="nsB")
    rows = dict(conn.execute(
        "SELECT namespace, value FROM memories WHERE key='shared.note'").fetchall())
    assert rows == {"nsA": "durable shared note for nsA",
                    "nsB": "durable shared note for nsB"}
    save(conn, "shared.note", "durable updated note for nsA", namespace="nsA")
    rows = dict(conn.execute(
        "SELECT namespace, value FROM memories WHERE key='shared.note'").fetchall())
    assert rows == {"nsA": "durable updated note for nsA",
                    "nsB": "durable shared note for nsB"}
    conn.close()


def test_backfill_pre_migration_rows_read_as_default(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript(OLD_SCHEMA)
    raw.commit()
    raw.close()
    conn = db.connect(path)
    assert db.migrate(conn) == 3
    assert conn.execute(
        "SELECT v FROM memo_meta WHERE k='memories_schema_5'").fetchone()[0] == "applied"
    assert conn.execute(
        "SELECT namespace FROM edges WHERE src_key='a'").fetchone()[0] == "default"
    assert conn.execute(
        "SELECT namespace FROM aliases WHERE segment='s'").fetchone()[0] == "default"
    assert conn.execute(
        "SELECT namespace FROM edge_declarers WHERE src_key='a'"
    ).fetchone()[0] == "default"
    info = conn.execute("PRAGMA table_info(edges)").fetchall()
    assert [r[1] for r in info if r[5]] == [
        "src_key", "dst_key", "rel", "namespace"]
    assert {r["key"] for r in neighborhood(conn, "a", 1)} == {"b"}
    conn.close()


def test_namespace_migration_is_idempotent(tmp_path):
    conn = mem(tmp_path)
    save(conn, "graph.alpha", "durable atlas note alpha", namespace="nsA",
         links=[{"to": "graph", "rel": "mentions"}], agent_id=AGENT_A)
    before = conn.execute("SELECT count(*) FROM edges").fetchone()[0]
    assert db.migrate(conn) == 3
    assert conn.execute("SELECT count(*) FROM edges").fetchone()[0] == before
    assert conn.execute(
        "SELECT v FROM memo_meta WHERE k='memories_schema_5'").fetchone()[0] == "applied"
    conn.close()
