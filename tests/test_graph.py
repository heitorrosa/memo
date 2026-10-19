import sqlite3

import pytest

from memo import db, embeddings
from memo.memory.graph import neighborhood
from memo.memory.save import save


@pytest.fixture(autouse=True)
def fallback():
    embeddings.force_fallback = True
    try:
        yield
    finally:
        embeddings.force_fallback = False


def mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def edges(conn):
    return conn.execute(
        "SELECT src_key, dst_key, rel, weight FROM edges"
        " ORDER BY src_key, dst_key, rel").fetchall()


def test_migration_creates_edges_table(tmp_path):
    conn = mem(tmp_path)
    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='edges'").fetchone() is not None
    info = conn.execute("PRAGMA table_info(edges)").fetchall()
    assert {r[1] for r in info} == {
        "src_key", "dst_key", "rel", "namespace", "weight", "created_at"}
    assert [r[1] for r in info if r[5]] == [
        "src_key", "dst_key", "rel", "namespace"]
    assert conn.execute(
        "SELECT v FROM memo_meta WHERE k='memories_schema_3'").fetchone()[0] == "applied"
    conn.close()


def test_edges_rel_check_rejects_unknown(tmp_path):
    conn = mem(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO edges(src_key,dst_key,rel,weight,created_at)"
            " VALUES('a','b','nope',1.0,'t')")
    conn.close()


def test_derived_from_emitted_on_split(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor.indent", "prefers tabs")
    r = save(conn, "editor.indent.v2", "prefers spaces instead of tabs")
    assert r["action"] == "superseded"
    assert ("editor.indent.v2", "editor.indent", "derived_from", 1.0) in edges(conn)
    conn.close()


def test_derived_from_emitted_on_qualifies(tmp_path):
    conn = mem(tmp_path)
    assert save(conn, "editor.tabs", "prefers tabs")["action"] == "created"
    r = save(conn, "editor.tabs2", "also prefers tabs in the editor")
    assert r["action"] == "created"
    assert ("editor.tabs2", "editor.tabs", "derived_from", 1.0) in edges(conn)
    conn.close()


def test_mentions_for_dotted_key(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor", "durable editor hub note alpha")
    save(conn, "editor.indent", "prefers tabs")
    assert ("editor.indent", "editor", "mentions", 1.0) in edges(conn)
    conn.close()


def test_no_mentions_when_prefix_is_not_a_memory(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor.indent", "prefers tabs")
    assert [row for row in edges(conn) if row[2] == "mentions"] == []
    conn.close()


def test_mentions_multi_segment_leading_only(tmp_path):
    conn = mem(tmp_path)
    save(conn, "homelab", "durable homelab hub note alpha")
    save(conn, "homelab.memory.x", "durable homelab memory note alpha")
    rows = edges(conn)
    assert ("homelab.memory.x", "homelab", "mentions", 1.0) in rows
    assert not any(row[1] == "homelab.memory" for row in rows)
    conn.close()


def test_natural_title_emits_no_mentions(tmp_path):
    conn = mem(tmp_path)
    assert save(conn, "meeting notes", "durable atlas fact about standup")["action"] == "created"
    assert [row for row in edges(conn) if row[2] == "mentions"] == []
    conn.close()


def test_edges_idempotent_on_resave(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor", "durable editor hub note alpha")
    save(conn, "editor.indent", "prefers tabs")
    save(conn, "editor.indent", "prefers tabs")
    assert [row for row in edges(conn) if row[2] == "mentions"] == [
        ("editor.indent", "editor", "mentions", 1.0)]
    conn.close()


def test_exact_key_update_emits_no_new_edges(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor", "durable editor hub note alpha")
    save(conn, "editor.indent", "prefers tabs")
    before = edges(conn)
    assert save(conn, "editor.indent", "prefers spaces")["action"] == "updated"
    assert edges(conn) == before
    conn.close()


def test_neighborhood_one_hop(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor", "durable editor hub note alpha")
    save(conn, "editor.indent", "prefers tabs")
    save(conn, "editor.theme", "uses a dark theme")
    assert [r["key"] for r in neighborhood(conn, "editor.indent", 1)] == ["editor"]
    conn.close()


def test_neighborhood_two_hop(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor", "durable editor hub note alpha")
    save(conn, "editor.indent", "prefers tabs")
    save(conn, "editor.theme", "uses a dark theme")
    result = neighborhood(conn, "editor.indent", 2)
    assert [(r["key"], r["hops"]) for r in result] == [("editor", 1), ("editor.theme", 2)]
    conn.close()


def test_neighborhood_excludes_superseded(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor", "durable editor hub note alpha")
    save(conn, "editor.indent", "prefers tabs")
    save(conn, "editor.indent.v2", "prefers spaces instead of tabs")
    keys = {r["key"] for r in neighborhood(conn, "editor.indent.v2", 1)}
    assert "editor.indent" not in keys
    assert "editor" in keys
    conn.close()


def test_neighborhood_excludes_expired(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor", "durable editor hub note alpha")
    save(conn, "editor.indent", "prefers tabs")
    save(conn, "editor.theme", "uses a dark theme", valid_until="2020-01-01")
    keys = {r["key"] for r in neighborhood(conn, "editor.indent", 2)}
    assert "editor" in keys
    assert "editor.theme" not in keys
    conn.close()


def test_neighborhood_deterministic_order(tmp_path):
    conn = mem(tmp_path)
    save(conn, "hub", "durable hub note alpha")
    save(conn, "hub.one", "durable hub note alpha")
    save(conn, "hub.two", "durable hub note beta")
    save(conn, "hub.three", "durable hub note gamma")
    first = neighborhood(conn, "hub", 1)
    assert first == neighborhood(conn, "hub", 1)
    assert [r["key"] for r in first] == ["hub.one", "hub.three", "hub.two"]
    conn.close()


def test_neighborhood_empty_graph(tmp_path):
    conn = mem(tmp_path)
    assert neighborhood(conn, "nothing.here", 2) == []
    conn.close()


def test_neighborhood_depth_bounds(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor", "durable editor hub note alpha")
    save(conn, "editor.indent", "prefers tabs")
    assert neighborhood(conn, "editor.indent", 0) == []
    assert neighborhood(conn, "editor.indent", 5) == neighborhood(conn, "editor.indent", 2)
    conn.close()
