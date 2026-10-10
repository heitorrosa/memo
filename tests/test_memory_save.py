"""Task 3 tests: memory_save ladder, classify, trivia gate (spec §§8-10)."""

import warnings

import pytest

from memo import db, embeddings
from memo.memory.save import save


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


def _row(conn, key, ns="default"):
    return conn.execute(
        "SELECT key, value, status, family_id, superseded_by, valid_to, "
        "relation_type, uses, score, stability, valid_from, created_at "
        "FROM memories WHERE key=? AND namespace=?", (key, ns)).fetchone()


def _count(conn):
    return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]


def test_exact_update(tmp_path):
    # Same key + new value = UPDATE, never supersede (§8.1).
    conn = _mem(tmp_path)
    assert save(conn, "editor.indent", "prefers tabs")["action"] == "created"
    r = save(conn, "editor.indent", "prefers spaces")
    assert (r["key"], r["action"]) == ("editor.indent", "updated")
    assert _count(conn) == 1
    row = _row(conn, "editor.indent")
    assert row[1] == "prefers spaces" and row[2] == "current"
    assert row[7] == 1  # uses+1
    conn.close()


def test_fuzzy_extends(tmp_path):
    # Similar key, additive info -> merged into old row, one row total.
    conn = _mem(tmp_path)
    save(conn, "editor.tabs", "prefers tabs")
    r = save(conn, "editor.tabs.pref", "prefers tabs in editor")
    assert r["action"] in ("updated", "created")
    assert _count(conn) == 1
    conn.close()


def test_contradict_supersedes(tmp_path):
    # Contradiction across keys: old superseded, same family, chain linked.
    conn = _mem(tmp_path)
    save(conn, "editor.indent", "prefers tabs")
    old = _row(conn, "editor.indent")
    r = save(conn, "editor.indent.v2", "prefers spaces instead of tabs")
    assert r["action"] == "superseded"
    new = _row(conn, "editor.indent.v2")
    assert new is not None and new[2] == "current"
    assert new[3] == old[3]  # same family_id
    upd = _row(conn, "editor.indent")
    assert upd[2] == "superseded" and upd[4] == "editor.indent.v2"
    assert upd[5] == new[10]  # valid_to(A) = valid_from(B), no gaps
    assert new[6] in ("supersedes", "corrects")  # relation lives on the winner
    conn.close()


def test_unrelated_coexists(tmp_path):
    conn = _mem(tmp_path)
    save(conn, "ml.frame", "uses PyTorch for ML")
    r = save(conn, "ml.research", "uses JAX for some research")
    assert r["action"] == "created"
    assert _count(conn) == 2
    a = _row(conn, "ml.frame")
    b = _row(conn, "ml.research")
    assert a[3] != b[3]  # NEW family
    conn.close()


def test_qualifies_no_status_change(tmp_path):
    conn = _mem(tmp_path)
    save(conn, "home.city", "lives in Campinas")
    save(conn, "home.city.scope", "also studies in Sao Paulo")
    old = _row(conn, "home.city")
    assert old[2] == "current"  # no status change on the old row
    conn.close()


def test_reasserts_same_row(tmp_path):
    # Time-relative restatement merges: one row, never duplicates (Review Focus).
    conn = _mem(tmp_path)
    save(conn, "editor.indent", "prefers spaces")
    r = save(conn, "editor.indent.note", "still prefers spaces as of 2026-10")
    assert r["action"] in ("updated", "created")
    assert _count(conn) == 1
    conn.close()


def test_trivia_rejected(tmp_path):
    # Trivia -> SUCCESS shape, do nothing, don't retry (M-#1 / §7).
    conn = _mem(tmp_path)
    for junk in ("ok", "hi", "thanks", "got it", "x"):
        r = save(conn, f"junk.{junk}", junk)
        assert r["action"] == "rejected_trivia", junk
    assert _count(conn) == 0
    conn.close()


def test_retry_idempotent(tmp_path):
    # Retry after timeout -> created then updated/merged, never two rows (T-7).
    conn = _mem(tmp_path)
    assert save(conn, "project.stack", "uses Bun not npm")[ "action"] == "created"
    assert save(conn, "project.stack", "uses Bun not npm")["action"] == "updated"
    assert _count(conn) == 1
    conn.close()


def test_future_valid_from_kept(tmp_path):
    # Future valid_from MUST NOT be clamped (M-010, T-8).
    conn = _mem(tmp_path)
    save(conn, "move.date", "moving to SP", valid_from="2027-01-01")
    row = _row(conn, "move.date")
    assert row[10].startswith("2027-01-01"), row[10]
    conn.close()


def test_oversize_accepted(tmp_path):
    # F-14: 10KB value accepted (truncation is display-only); warn past 4KB.
    conn = _mem(tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        r = save(conn, "big.note", "durable project context: " + "x" * 10_000)
    assert r["action"] == "created"
    assert any("4KB" in str(w.message) or "large" in str(w.message).lower() for w in caught)
    assert len(_row(conn, "big.note")[1]) > 10_000
    conn.close()
