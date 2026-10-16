import datetime

import numpy as np
import pytest

from memo import db, embeddings, temporal
from memo.memory.save import save
from memo.memory.search import search

QUERY = "quantum zephyr quokka"
NEEDLE_KEY = "scale.needle"
NEEDLE_VALUE = "quantum zephyr quokka durable fact"
FILLERS = 700
HISTORICAL = 20


@pytest.fixture(autouse=True)
def fallback():
    embeddings.force_fallback = True
    try:
        yield
    finally:
        embeddings.force_fallback = False


def mem(tmp_path, name="memo.db"):
    conn = db.connect(str(tmp_path / name))
    assert db.migrate(conn) == 3
    return conn


def orthogonal_to(vector):
    base = np.frombuffer(vector, dtype=np.float32)
    rng = np.random.RandomState(7)
    filler = rng.rand(embeddings.DIM).astype(np.float32)
    filler -= float(np.dot(filler, base)) * base
    return (filler / (np.linalg.norm(filler) + 1e-12)).astype(np.float32).tobytes()


def insert_row(conn, key, value, embedding, status, valid_from, valid_to=None,
               valid_until=None, created_at=None, kind="context", stability=3.0):
    when = created_at or valid_from
    conn.execute(
        "INSERT INTO memories(key,namespace,value,type,embedding,stability,score,uses,"
        "created_at,updated_at,valid_from,valid_to,valid_until,agent_id,status,family_id,"
        "superseded_by,relation_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, "default", value, kind, embedding, stability, 1.0, 0, when, when,
         valid_from, valid_to, valid_until, None, status, key, None, None))


def seed_scale(conn):
    """721 rows in one namespace: 700 unrelated fillers, 20 superseded
    historical rows, then the relevant needle inserted last (highest rowid)."""
    needle = embeddings.embed([QUERY])[0]
    filler = orthogonal_to(needle)
    old = (temporal.parse_ts(temporal.now_iso())
           - datetime.timedelta(days=300)).isoformat()
    for n in range(FILLERS):
        insert_row(conn, f"filler.note.{n:04d}", f"fillerword{n:04d} routine note",
                   filler, "current", old)
    for n in range(HISTORICAL):
        insert_row(conn, f"hist.old.{n:04d}", f"retiredword{n:04d} historical note",
                   filler, "superseded", old, valid_to=old)
    insert_row(conn, NEEDLE_KEY, NEEDLE_VALUE, needle, "current", old,
               kind="preference", stability=14.0)
    conn.commit()


def run_scale(tmp_path, name):
    conn = mem(tmp_path, f"scale_{name}.db")
    seed_scale(conn)
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert total == FILLERS + HISTORICAL + 1 > 500
    keys = [hit["key"] for hit in search(conn, QUERY, limit=10)]
    conn.close()
    return keys


def test_scale_needle_reaches_ranking_past_bound(tmp_path):
    first = run_scale(tmp_path, "a")
    second = run_scale(tmp_path, "b")
    assert NEEDLE_KEY in first
    assert first[0] == NEEDLE_KEY
    assert first == second


def graph_linked_superseded(tmp_path, name):
    """editor.indent (old, superseded) is a graph neighbour of the eligible
    editor.notes; the superseding row editor.indent.v2 is future-dated."""
    conn = mem(tmp_path, name)
    save(conn, "editor.indent", "editor indent tabs legacy", type="preference",
         valid_from="2026-01-01")
    save(conn, "editor.indent.v2", "editor indent spaces instead of tabs",
         type="preference", valid_from="2027-01-01")
    save(conn, "editor.notes", "quokka anchor durable fact", type="preference",
         links=[{"to": "editor.indent", "rel": "derived_from"}])
    status = dict(conn.execute("SELECT key,status FROM memories"))
    edges = {(src, dst) for src, dst in
             conn.execute("SELECT src_key,dst_key FROM edges")}
    return conn, status, edges


def test_future_dated_superseded_neighbor_excluded_by_default(tmp_path):
    conn, status, edges = graph_linked_superseded(tmp_path, "sup_default.db")
    keys = [hit["key"] for hit in search(conn, "quokka")]
    conn.close()
    assert status["editor.indent"] == "superseded"
    assert ("editor.notes", "editor.indent") in edges
    assert "editor.indent" not in keys
    assert "editor.indent.v2" not in keys


def test_superseded_row_included_only_with_include_superseded(tmp_path):
    conn, status, edges = graph_linked_superseded(tmp_path, "sup_included.db")
    default_keys = [hit["key"] for hit in search(conn, "quokka")]
    included_keys = [hit["key"] for hit in search(conn, "quokka",
                                                  include_superseded=True)]
    conn.close()
    assert ("editor.notes", "editor.indent") in edges
    assert "editor.indent" not in default_keys
    assert "editor.indent" in included_keys


def test_expansion_respects_as_known_at_policy(tmp_path):
    conn = mem(tmp_path, "known.db")
    save(conn, "lab.anchor", "quokka anchor", type="preference",
         valid_from="2026-01-01")
    save(conn, "lab.child", "childunit value", type="preference",
         valid_from="2026-01-01",
         links=[{"to": "lab.anchor", "rel": "derived_from"}])
    # anchor existed before `known`; child was created after it.
    conn.execute("UPDATE memories SET created_at='2026-09-01T00:00:00+00:00'"
                 " WHERE key='lab.anchor'")
    conn.execute("UPDATE memories SET created_at='2026-10-05T00:00:00+00:00'"
                 " WHERE key='lab.child'")
    conn.commit()
    as_known = [hit["key"] for hit in search(conn, "quokka anchor",
                                             as_known_at="2026-10-01")]
    present = [hit["key"] for hit in search(conn, "quokka anchor")]
    conn.close()
    assert "lab.child" in present
    assert "lab.child" not in as_known


def test_expansion_honors_historical_valid_at_window(tmp_path):
    conn = mem(tmp_path, "window.db")
    save(conn, "lab.anchor", "quokka anchor", type="preference",
         valid_from="2026-01-01")
    save(conn, "lab.old", "oldunit value", type="preference",
         valid_from="2026-01-01", valid_until="2026-02-01",
         links=[{"to": "lab.anchor", "rel": "derived_from"}])
    on = {hit["key"]: hit["score"] for hit in search(
        conn, "quokka anchor", valid_at="2026-01-15")}
    present = [hit["key"] for hit in search(conn, "quokka anchor")]
    conn.close()
    assert on["lab.old"] > 0
    assert "lab.old" not in present


def test_expansion_include_superseded_with_valid_at(tmp_path):
    conn = mem(tmp_path, "combined.db")
    embedding = embeddings.embed(["x"])[0]
    insert_row(conn, "sup.recent", "quokka zephyr note", embedding, "superseded",
               "2026-09-01", valid_to="2026-10-01")
    insert_row(conn, "sup.closed", "quokka quokka note", embedding, "superseded",
               "2026-01-01", valid_until="2026-04-01")
    conn.commit()
    past_keys = [hit["key"] for hit in search(
        conn, "quokka", valid_at="2026-05-01", include_superseded=True)]
    window_keys = [hit["key"] for hit in search(
        conn, "quokka", valid_at="2026-09-15", include_superseded=True)]
    default_keys = [hit["key"] for hit in search(
        conn, "quokka", valid_at="2026-09-15")]
    conn.close()
    assert "sup.recent" not in past_keys
    assert "sup.closed" not in past_keys
    assert "sup.recent" in window_keys
    assert "sup.closed" not in window_keys
    assert "sup.recent" not in default_keys
