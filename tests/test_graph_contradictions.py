import pytest

from memo import db, embeddings, temporal
from memo.memory.save import save
from memo.memory.search import search

FROZEN_NOW = "2026-10-16T12:00:00-03:00"
QUERY = "display"
PAIR_A = "theme.dark.choice"
PAIR_B = "glare.mode.setting"
AGENT_A, AGENT_B = "A" * 8, "B" * 8
PRE_PHASE2_SHAPE = {"key", "value", "type", "score", "age_days", "uses",
                    "updated_at"}


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


def seed_pair(conn, agents=(AGENT_A,), valid_until=None):
    save(conn, PAIR_A, "also display dark theme")
    for agent in agents:
        save(conn, PAIR_B, "also display light theme", valid_until=valid_until,
             links=[{"to": PAIR_A, "rel": "contradicts"}], agent_id=agent)


def by_key(conn, **kwargs):
    return {hit["key"]: hit for hit in search(conn, QUERY, **kwargs)}


def test_contradicting_pair_surfaces_both_sides(tmp_path):
    conn = mem(tmp_path)
    seed_pair(conn, agents=(AGENT_A, AGENT_B))
    hits = by_key(conn)
    assert {PAIR_A, PAIR_B} <= set(hits)
    assert hits[PAIR_A]["contradictions"] == [
        {"key": PAIR_B, "declarers": [AGENT_A, AGENT_B], "weight": 0.0}]
    assert hits[PAIR_B]["contradictions"] == [
        {"key": PAIR_A, "declarers": [AGENT_A, AGENT_B], "weight": 0.0}]
    conn.close()


def test_no_score_boost_scores_equal_without_contradicts_edges(tmp_path):
    conn = mem(tmp_path)
    seed_pair(conn, agents=(AGENT_A, AGENT_B))
    flagged = by_key(conn)
    assert flagged[PAIR_A]["contradictions"] and flagged[PAIR_B]["contradictions"]
    before = {key: hit["score"] for key, hit in flagged.items()}
    conn.execute("DELETE FROM edges WHERE rel='contradicts'")
    conn.commit()
    after = by_key(conn)
    assert set(after) == set(before)
    for key, score in before.items():
        assert after[key]["score"] == pytest.approx(score)
    assert all("contradictions" not in hit for hit in after.values())
    conn.close()


def test_no_flag_without_contradicts_edge(tmp_path):
    conn = mem(tmp_path)
    save(conn, PAIR_A, "also display dark theme")
    save(conn, PAIR_B, "also display light theme",
         links=[{"to": PAIR_A, "rel": "supports"}], agent_id=AGENT_A)
    hits = by_key(conn)
    assert {PAIR_A, PAIR_B} <= set(hits)
    assert all("contradictions" not in hit for hit in hits.values())
    conn.close()


def test_superseded_counterpart_hidden_and_unflagged(tmp_path):
    conn = mem(tmp_path)
    seed_pair(conn, agents=(AGENT_A, AGENT_B))
    conn.execute("UPDATE memories SET status='superseded', valid_to=? WHERE key=?",
                 (FROZEN_NOW, PAIR_B))
    conn.commit()
    hits = by_key(conn)
    assert PAIR_B not in hits
    assert PAIR_A in hits
    assert "contradictions" not in hits[PAIR_A]
    conn.close()


def test_expired_counterpart_excluded_from_flags(tmp_path):
    conn = mem(tmp_path)
    seed_pair(conn, valid_until="2026-10-01")
    hits = by_key(conn)
    assert PAIR_B not in hits
    assert PAIR_A in hits
    assert "contradictions" not in hits[PAIR_A]
    conn.close()


def test_declarers_listed_from_edge_declarers(tmp_path):
    conn = mem(tmp_path)
    seed_pair(conn, agents=(AGENT_A,))
    hits = by_key(conn)
    assert hits[PAIR_A]["contradictions"] == [
        {"key": PAIR_B, "declarers": [AGENT_A], "weight": 0.0}]
    stored = conn.execute(
        "SELECT declarers FROM edge_declarers WHERE src_key=? AND dst_key=?"
        " AND rel='contradicts'", (PAIR_B, PAIR_A)).fetchone()
    assert stored[0] == AGENT_A
    conn.close()


def test_agreement_upgrade_reflected_in_flag(tmp_path):
    conn = mem(tmp_path)
    seed_pair(conn, agents=(AGENT_A,))
    provisional = by_key(conn)[PAIR_A]["contradictions"][0]
    assert provisional["declarers"] == [AGENT_A]
    save(conn, PAIR_B, "also display light theme",
         links=[{"to": PAIR_A, "rel": "contradicts"}], agent_id=AGENT_B)
    assert conn.execute(
        "SELECT status FROM edge_declarers WHERE rel='contradicts'"
    ).fetchone()[0] == "confirmed"
    confirmed = by_key(conn)[PAIR_A]["contradictions"][0]
    assert confirmed["declarers"] == [AGENT_A, AGENT_B]
    assert provisional["weight"] == confirmed["weight"] == 0.0
    conn.close()


def test_empty_graph_hits_keep_pre_phase2_shape(tmp_path):
    conn = mem(tmp_path)
    save(conn, "sandboxalpha", "also display alpha")
    save(conn, "sandboxbravo", "also display bravo")
    assert conn.execute("SELECT count(*) FROM edges").fetchone()[0] == 0
    hits = search(conn, QUERY)
    assert {hit["key"] for hit in hits} == {"sandboxalpha", "sandboxbravo"}
    for hit in hits:
        assert set(hit) == PRE_PHASE2_SHAPE
    assert search(conn, "   ")["error"] == "empty_query"
    conn.close()


def test_only_ranking_side_carries_flag_without_force_include(tmp_path):
    conn = mem(tmp_path)
    seed_pair(conn, agents=(AGENT_A,))
    hits = search(conn, QUERY, limit=1)
    assert len(hits) == 1
    only = hits[0]
    other = PAIR_B if only["key"] == PAIR_A else PAIR_A
    assert only["contradictions"] == [
        {"key": other, "declarers": [AGENT_A], "weight": 0.0}]
    conn.close()


def test_include_superseded_flags_only_the_valid_counterpart(tmp_path):
    conn = mem(tmp_path)
    seed_pair(conn, agents=(AGENT_A,))
    conn.execute("UPDATE memories SET status='superseded', valid_to=? WHERE key=?",
                 (FROZEN_NOW, PAIR_B))
    conn.commit()
    hits = by_key(conn, include_superseded=True)
    assert {PAIR_A, PAIR_B} <= set(hits)
    assert hits[PAIR_B]["contradictions"] == [
        {"key": PAIR_A, "declarers": [AGENT_A], "weight": 0.0}]
    assert "contradictions" not in hits[PAIR_A]
    conn.close()
