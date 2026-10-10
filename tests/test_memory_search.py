"""Task 4 tests: search read path, temporal, delete, bootstrap (spec §§11,14-15,22)."""

import pytest

from memo import db, embeddings, summary
from memo.memory.remove import remove
from memo.memory.save import save
from memo.memory.search import search
from memo.skills.crud import put


@pytest.fixture(autouse=True)
def _fallback():
    embeddings.force_fallback = True
    try:
        yield
    finally:
        embeddings.force_fallback = False


def _mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def _db_row(conn, key, ns="default"):
    return conn.execute(
        "SELECT uses, score, stability FROM memories WHERE key=? AND namespace=?",
        (key, ns)).fetchone()


def _contradict_pair(conn):
    save(conn, "editor.indent", "prefers tabs")
    save(conn, "editor.indent.v2", "prefers spaces instead of tabs")


def test_roundtrip_top1(tmp_path):
    conn = _mem(tmp_path)
    save(conn, "editor.indent", "prefers tabs", type="preference")
    hits = search(conn, "editor tabs indentation")
    assert hits and hits[0]["key"] == "editor.indent"
    assert set(hits[0]) >= {"key", "value", "type", "score", "age_days", "uses"}
    conn.close()


def test_empty_query(tmp_path):
    conn = _mem(tmp_path)
    assert search(conn, "   ")["error"] == "empty_query"
    conn.close()


def test_default_hides_superseded(tmp_path):
    conn = _mem(tmp_path)
    _contradict_pair(conn)
    hits = search(conn, "indentation tabs spaces")
    assert [h["key"] for h in hits] == ["editor.indent.v2"]
    conn.close()


def test_include_superseded_flagged(tmp_path):
    conn = _mem(tmp_path)
    _contradict_pair(conn)
    hits = search(conn, "indentation tabs spaces", include_superseded=True)
    assert {h["key"] for h in hits} == {"editor.indent", "editor.indent.v2"}
    conn.close()


def test_valid_at_future_excludes(tmp_path):
    # Future-dated row is current-but-not-valid: now excludes it (T-8).
    conn = _mem(tmp_path)
    save(conn, "move.date", "moving to Sao Paulo", valid_from="2027-01-01")
    assert search(conn, "moving Sao Paulo") == []
    hits = search(conn, "moving Sao Paulo", valid_at="2027-06-01")
    assert [h["key"] for h in hits] == ["move.date"]
    conn.close()


def test_valid_at_past_window(tmp_path):
    # §11 table: past T sees A only via include+window; default sees neither.
    conn = _mem(tmp_path)
    r = save(conn, "editor.indent", "prefers tabs", valid_from="2026-01-10")
    assert r["action"] == "created"
    save(conn, "editor.indent.v2", "prefers spaces instead of tabs",
                valid_from="2026-09-20")
    past = "2026-05-01"
    inc = search(conn, "indentation", valid_at=past, include_superseded=True)
    assert [h["key"] for h in inc] == ["editor.indent"]
    assert search(conn, "indentation", valid_at=past) == []
    conn.close()


def test_as_known_at(tmp_path):
    # Believed-then: K before the supersede sees A as current.
    conn = _mem(tmp_path)
    save(conn, "editor.indent", "prefers tabs")
    ca = conn.execute(
        "SELECT created_at FROM memories WHERE key='editor.indent'").fetchone()[0]
    save(conn, "editor.indent.v2", "prefers spaces instead of tabs")
    cb = conn.execute(
        "SELECT created_at FROM memories WHERE key='editor.indent.v2'").fetchone()[0]
    assert ca < cb
    hits = search(conn, "indentation", as_known_at=ca)
    assert [h["key"] for h in hits] == ["editor.indent"]
    conn.close()


def test_touch_once(tmp_path):
    # Exactly once per returned row: uses+1, score×1.1, S-growth (M-005).
    conn = _mem(tmp_path)
    save(conn, "editor.indent", "prefers tabs", type="preference")
    search(conn, "editor tabs")
    uses, score, stab = _db_row(conn, "editor.indent")
    assert (uses, round(score, 6)) == (1, 1.1)
    assert stab == pytest.approx(14.0 * 1.3)  # R(0)=1: full beta growth
    search(conn, "editor tabs")
    assert _db_row(conn, "editor.indent")[0] == 2  # never +2 per call
    conn.close()


def test_limit_cap(tmp_path):
    conn = _mem(tmp_path)
    for i in range(3):
        save(conn, f"fact.{i}", f"durable project fact number {i}")
    assert len(search(conn, "durable project fact", limit=1)) == 1
    assert len(search(conn, "durable project fact", limit=500)) <= 50
    conn.close()


def test_delete_soft(tmp_path):
    conn = _mem(tmp_path)
    save(conn, "old.habit", "bites nails")
    assert remove(conn, "old.habit")["action"] == "deleted"
    assert conn.execute(
        "SELECT COUNT(*) FROM memories WHERE key='old.habit'").fetchone()[0] == 0
    arch = conn.execute(
        "SELECT archived_reason FROM archived_memories WHERE key='old.habit'").fetchone()
    assert arch and arch[0] == "deleted"
    conn.close()


def test_delete_missing_not_found(tmp_path):
    # Forgetting the forgotten is success-shaped, NOT an error (M-004).
    conn = _mem(tmp_path)
    assert remove(conn, "ghost") == {"key": "ghost", "action": "not_found"}
    conn.close()


def test_delete_keeps_family(tmp_path):
    conn = _mem(tmp_path)
    _contradict_pair(conn)
    remove(conn, "editor.indent.v2")
    hits = search(conn, "indentation", include_superseded=True)
    assert [h["key"] for h in hits] == ["editor.indent"]  # history intact
    conn.close()


def test_bootstrap_shape(tmp_path):
    conn = _mem(tmp_path)
    save(conn, "editor.indent", "prefers tabs", type="preference")
    put(conn, "qol-deploy", "Ship the app.", "# deploy")
    text = summary.bootstrap_text(conn)
    blocks = ["# memo bootstrap", "## Orientation", "## Top memories (",
              "## Skill index (", "## Stats: 1/250 memories"]
    idx = [text.index(b) for b in blocks]
    assert idx == sorted(idx)  # exact block order (§22)
    assert "editor.indent" in text and "qol-deploy" in text
    conn.close()


def test_cross_namespace_isolation(tmp_path):
    conn = _mem(tmp_path)
    save(conn, "note", "default namespace fact here", namespace="default")
    save(conn, "note", "work namespace fact here", namespace="work")
    hits = search(conn, "namespace fact", namespace="work")
    assert hits and all(h["key"] == "note" for h in hits)
    assert hits[0]["value"] == "work namespace fact here"
    conn.close()


def test_date_only_coercion(tmp_path):
    # §32: date-only valid_from = that month's first day for predicates.
    conn = _mem(tmp_path)
    save(conn, "party.date", "anniversary party", valid_from="2026-10")
    vf = conn.execute(
        "SELECT valid_from FROM memories WHERE key='party.date'").fetchone()[0]
    assert vf.startswith("2026-10-01"), vf
    assert search(conn, "anniversary", valid_at="2026-09-15") == []
    assert [h["key"] for h in search(
        conn, "anniversary", valid_at="2026-10-15")] == ["party.date"]
    conn.close()


def test_monotonic_guard(tmp_path):
    # F-15: clock jumps back -> updated_at never regresses.
    conn = _mem(tmp_path)
    save(conn, "editor.indent", "prefers tabs")
    conn.execute("UPDATE memories SET updated_at='2999-01-01T00:00:00+00:00'"
                 " WHERE key='editor.indent'")
    conn.commit()
    save(conn, "editor.indent", "prefers spaces")
    ua = conn.execute(
        "SELECT updated_at FROM memories WHERE key='editor.indent'").fetchone()[0]
    assert ua == "2999-01-01T00:00:00+00:00"
    conn.close()
