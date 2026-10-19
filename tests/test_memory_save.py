import warnings

import pytest

from memo import db, embeddings
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


def fetch_row(conn, key, ns="default"):
    return conn.execute(
        "SELECT key, value, status, family_id, superseded_by, valid_to, "
        "relation_type, uses, score, stability, valid_from, created_at "
        "FROM memories WHERE key=? AND namespace=?", (key, ns)).fetchone()


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]


def test_exact_update(tmp_path):
    conn = mem(tmp_path)
    assert save(conn, "editor.indent", "prefers tabs")["action"] == "created"
    r = save(conn, "editor.indent", "prefers spaces")
    assert (r["key"], r["action"]) == ("editor.indent", "updated")
    assert count(conn) == 1
    row = fetch_row(conn, "editor.indent")
    assert row[1] == "prefers spaces" and row[2] == "current"
    assert row[7] == 1
    conn.close()


def test_fuzzy_extends(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor.tabs", "prefers tabs")
    r = save(conn, "editor.tabs.pref", "prefers tabs in editor")
    assert r["action"] in ("updated", "created")
    assert count(conn) == 1
    conn.close()


def test_contradict_supersedes(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor.indent", "prefers tabs")
    old = fetch_row(conn, "editor.indent")
    r = save(conn, "editor.indent.v2", "prefers spaces instead of tabs")
    assert r["action"] == "superseded"
    new = fetch_row(conn, "editor.indent.v2")
    assert new is not None and new[2] == "current"
    assert new[3] == old[3]
    upd = fetch_row(conn, "editor.indent")
    assert upd[2] == "superseded" and upd[4] == "editor.indent.v2"
    assert upd[5] == new[10]
    assert new[6] in ("supersedes", "corrects")
    conn.close()


def test_unrelated_coexists(tmp_path):
    conn = mem(tmp_path)
    save(conn, "ml.frame", "uses PyTorch for ML")
    r = save(conn, "ml.research", "uses JAX for some research")
    assert r["action"] == "created"
    assert count(conn) == 2
    a = fetch_row(conn, "ml.frame")
    b = fetch_row(conn, "ml.research")
    assert a[3] != b[3]
    conn.close()


def test_qualifies_no_status_change(tmp_path):
    conn = mem(tmp_path)
    save(conn, "home.city", "lives in Campinas")
    save(conn, "home.city.scope", "also studies in Sao Paulo")
    old = fetch_row(conn, "home.city")
    assert old[2] == "current"
    conn.close()


def test_reasserts_same_row(tmp_path):
    conn = mem(tmp_path)
    save(conn, "editor.indent", "prefers spaces")
    r = save(conn, "editor.indent.note", "still prefers spaces as of 2026-10")
    assert r["action"] in ("updated", "created")
    assert count(conn) == 1
    conn.close()


def test_trivia_rejected(tmp_path):
    conn = mem(tmp_path)
    for junk in ("ok", "hi", "thanks", "got it", "x"):
        r = save(conn, f"junk.{junk}", junk)
        assert r["action"] == "rejected_trivia", junk
    assert count(conn) == 0
    conn.close()


def test_retry_idempotent(tmp_path):
    conn = mem(tmp_path)
    assert save(conn, "project.stack", "uses Bun not npm")[ "action"] == "created"
    assert save(conn, "project.stack", "uses Bun not npm")["action"] == "updated"
    assert count(conn) == 1
    conn.close()


def test_future_valid_from_kept(tmp_path):
    conn = mem(tmp_path)
    save(conn, "move.date", "moving to SP", valid_from="2027-01-01")
    row = fetch_row(conn, "move.date")
    assert row[10].startswith("2027-01-01"), row[10]
    conn.close()


def test_oversize_accepted(tmp_path):
    conn = mem(tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        r = save(conn, "big.note", "durable project context: " + "x" * 10_000)
    assert r["action"] == "created"
    assert any("4KB" in str(w.message) or "large" in str(w.message).lower() for w in caught)
    assert len(fetch_row(conn, "big.note")[1]) > 10_000
    conn.close()


def test_bad_type_rejected_invalid_input(tmp_path):
    from memo.memory.save import original_save
    conn = mem(tmp_path)
    r = original_save(conn, "context.note", "durable fact here", type="nonsense")
    assert r == {"error": "invalid_input", "detail": "bad type 'nonsense'"}
    assert count(conn) == 0
    conn.close()


def test_type_prefix_mismatch_rejected(tmp_path):
    conn = mem(tmp_path)
    r = save(conn, "preference.theme", "durable fact here", type="context")
    assert r["error"] == "invalid_input"
    assert "preference" in r["detail"] and "context" in r["detail"]
    assert count(conn) == 0
    conn.close()


def test_legacy_dotted_prefix_allowed(tmp_path):
    conn = mem(tmp_path)
    assert save(conn, "editor.indent", "prefers tabs")["action"] == "created"
    assert save(conn, "correction.tip", "durable feedback note",
                type="feedback")["action"] == "created"
    conn.close()


def test_dotless_natural_title_advisory(tmp_path):
    conn = mem(tmp_path)
    r = save(conn, "meeting notes", "durable atlas fact about standup")
    assert r["action"] == "created"
    assert "natural_title" in r.get("suggestion", "")
    conn.close()


def test_date_in_key_advisory(tmp_path):
    conn = mem(tmp_path)
    r = save(conn, "context.review-2026-10", "durable atlas fact about review")
    assert r["action"] == "created"
    assert "date_in_key" in r.get("suggestion", "")
    conn.close()


def test_value_date_prefix_advisory(tmp_path):
    conn = mem(tmp_path)
    r = save(conn, "context.launch", "[2026-10-01] durable atlas launch note")
    assert r["action"] == "created"
    assert "value_date_prefix" in r.get("suggestion", "")
    conn.close()


def test_combined_advisories_join_with_too_dense(tmp_path):
    conn = mem(tmp_path)
    r = save(conn, "Review 2026-10", "x" * 600)
    assert r["action"] == "created"
    suggestion = r.get("suggestion", "")
    assert "natural_title" in suggestion
    assert "date_in_key" in suggestion
    assert "too_dense" in suggestion
    conn.close()


def test_do_memory_save_forwards_agent_id(tmp_path):
    from memo.server.operations import do_memory_save
    conn = mem(tmp_path)
    writer = "a1" * 16
    r = do_memory_save(conn, "context.note", "durable writer note alpha",
                       agent_id=writer)
    assert r["action"] == "created"
    row = conn.execute(
        "SELECT agent_id FROM memories WHERE key='context.note'").fetchone()
    assert row[0] == writer
    r2 = do_memory_save(conn, "context.note", "durable writer note beta")
    assert r2["action"] == "updated"
    row = conn.execute(
        "SELECT agent_id FROM memories WHERE key='context.note'").fetchone()
    assert row[0] == writer
    conn.close()


def test_do_memory_save_bad_type_is_invalid_input(tmp_path):
    from memo.server.operations import do_memory_save
    conn = mem(tmp_path)
    assert do_memory_save(conn, "k", "durable value here", typ="nonsense") == {
        "error": "invalid_input", "detail": "bad type 'nonsense'"}
    assert count(conn) == 0
    conn.close()


def mentions(conn):
    return conn.execute(
        "SELECT src_key, dst_key, rel FROM edges WHERE rel='mentions'"
        " ORDER BY src_key, dst_key").fetchall()


def test_no_mentions_edge_when_prefix_is_not_a_memory(tmp_path):
    conn = mem(tmp_path)
    save(conn, "preference.frontend-stack-core",
         "durable frontend stack core note alpha")
    assert mentions(conn) == []
    conn.close()


def test_mentions_edge_when_prefix_is_a_memory(tmp_path):
    conn = mem(tmp_path)
    save(conn, "memo", "durable memo hub note alpha")
    save(conn, "memo.dashboard.packaging", "durable dashboard packaging note beta")
    assert ("memo.dashboard.packaging", "memo", "mentions") in mentions(conn)
    conn.close()


def test_mentions_edge_needs_the_prefix_in_the_same_namespace(tmp_path):
    conn = mem(tmp_path)
    save(conn, "memo", "durable memo hub note alpha", namespace="one")
    save(conn, "memo.dashboard.packaging", "durable dashboard packaging note beta",
         namespace="two")
    assert mentions(conn) == []
    conn.close()
