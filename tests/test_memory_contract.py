"""Claude feedback package: expiry/TTL, density advisory, search filters,
writer provenance, validation codes, migration, description contracts."""

import sqlite3

import pytest

from memo import db, embeddings
from memo import prompts as prompt_store
from memo.memory.save import save
from memo.memory.search import search


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


# --- #3 expiry/TTL (temporal owns the predicate, search enforces it) ---

def test_expiry_boundary(tmp_path):
    conn = mem(tmp_path)
    save(conn, "temp.offer", "seasonal discount offer for atlas project",
         valid_from="2026-10-01", valid_until="2026-10-05")
    assert [h["key"] for h in search(
        conn, "discount offer atlas", valid_at="2026-10-04")] == ["temp.offer"]
    # Boundary: valid_until <= T counts as expired (mirrors valid_to).
    assert search(conn, "discount offer atlas", valid_at="2026-10-05") == []
    assert search(conn, "discount offer atlas", valid_at="2026-10-06") == []
    # valid_from still honored: before the window, nothing ranks.
    assert search(conn, "discount offer atlas", valid_at="2026-09-30") == []
    conn.close()


def test_expiry_window_now(tmp_path):
    conn = mem(tmp_path)
    save(conn, "move.plan", "relocation plan for atlas project",
         valid_from="2027-01-01", valid_until="2027-06-01")
    assert search(conn, "relocation atlas") == []  # future, not yet valid
    assert [h["key"] for h in search(
        conn, "relocation atlas", valid_at="2027-03-01")] == ["move.plan"]
    assert search(conn, "relocation atlas", valid_at="2027-07-01") == []
    save(conn, "old.promo", "expired promo for atlas project",
         valid_until="2020-01-01")
    assert search(conn, "promo atlas") == []  # long past, decayed
    save(conn, "long.policy", "standing policy for atlas project",
         valid_until="2030-01-01")
    assert [h["key"] for h in search(
        conn, "standing policy atlas")] == ["long.policy"]
    conn.close()


def test_garbage_valid_until_means_no_expiry(tmp_path):
    conn = mem(tmp_path)
    save(conn, "steady.note", "durable atlas project note that persists",
         valid_until="someday")
    row = conn.execute(
        "SELECT valid_until FROM memories WHERE key='steady.note'").fetchone()
    assert row[0] is None
    assert [h["key"] for h in search(
        conn, "durable atlas note")] == ["steady.note"]
    conn.close()


# --- #4 density advisory (never a rejection) ---

def test_too_dense_advisory(tmp_path):
    conn = mem(tmp_path)
    big = save(conn, "big.plan", "durable atlas context: " + "x" * 600)
    assert big["action"] == "created"  # still lands
    assert "too_dense" in big.get("suggestion", "")
    small = save(conn, "small.plan", "tiny durable atlas fact")
    assert small["action"] == "created"
    assert "suggestion" not in small
    conn.close()


def test_density_boundary(tmp_path):
    conn = mem(tmp_path)
    assert "suggestion" not in save(conn, "d.500", "q" * 500)
    assert "too_dense" in save(conn, "d.501", "q" * 501).get("suggestion", "")
    conn.close()


# --- #6 search filters + updated_at ---

def test_type_and_min_score_filters(tmp_path):
    conn = mem(tmp_path)
    save(conn, "flt.pref", "atlas project durable note alpha", type="preference")
    save(conn, "flt.ctx", "atlas project durable note beta", type="context")
    assert [h["key"] for h in search(
        conn, "atlas project durable note", type="preference")] == ["flt.pref"]
    assert search(conn, "atlas project durable note",
                  type="nonsense") == []
    hits = search(conn, "atlas project durable note", min_score=0.0)
    assert {h["key"] for h in hits} >= {"flt.pref"}
    assert all(h["score"] >= 0.0 for h in hits)
    top = max(h["score"] for h in hits)
    assert search(conn, "atlas project durable note",
                  min_score=999.0) == []
    # Positive engagement below the top hit (halved: clear of float noise
    # from the touch loop's stability growth between calls).
    assert {h["key"] for h in search(
        conn, "atlas project durable note", min_score=top / 2)} >= {"flt.pref"}
    assert "updated_at" in hits[0] and hits[0]["updated_at"]
    conn.close()


# --- #7 writer provenance stamp ---

def test_writer_stamp(tmp_path):
    conn = mem(tmp_path)
    writer_a, writer_b = "a1" * 16, "b2" * 16
    save(conn, "w.note", "durable writer note alpha", agent_id=writer_a)
    row = conn.execute(
        "SELECT agent_id FROM memories WHERE key='w.note'").fetchone()
    assert row[0] == writer_a
    save(conn, "w.note", "durable writer note beta")  # omitted: preserved
    row = conn.execute(
        "SELECT agent_id FROM memories WHERE key='w.note'").fetchone()
    assert row[0] == writer_a
    save(conn, "w.note", "durable writer note gamma", agent_id=writer_b)
    row = conn.execute(
        "SELECT agent_id FROM memories WHERE key='w.note'").fetchone()
    assert row[0] == writer_b
    conn.close()


# --- #11 validation code, never internal ---

def test_bad_temporal_input_is_invalid_input(tmp_path):
    from memo.server.operations import do_memory_search
    conn = mem(tmp_path)
    assert search(conn, "atlas", valid_at="garbage") == {
        "error": "invalid_input", "detail": "bad valid_at 'garbage'"}
    assert search(conn, "atlas", as_known_at="garbage") == {
        "error": "invalid_input", "detail": "bad as_known_at 'garbage'"}
    assert do_memory_search(conn, "atlas", valid_at="garbage")["error"] == \
        "invalid_input"
    conn.close()


# --- migration: pre-package DBs gain the columns, rows preserved ---

OLD_COLS = ("key, namespace, value, type, embedding, stability, score, uses,"
            " created_at, updated_at, valid_from, valid_to, status, family_id,"
            " superseded_by, relation_type")


def test_migration_adds_expiry_provenance(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.execute(f"CREATE TABLE memories({OLD_COLS}, PRIMARY KEY(key, namespace))")
    raw.execute("CREATE TABLE memo_meta(k TEXT PRIMARY KEY, v TEXT)")
    raw.execute(
        f"INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("legacy.key", "default", "legacy durable fact", "context", b"emb",
         3.0, 1.0, 0, "2026-01-01T00:00:00", "2026-01-01T00:00:00",
         "2026-01-01T00:00:00", None, "current", "fam1", None, None))
    raw.commit()
    raw.close()
    conn = db.connect(path)
    assert db.migrate(conn) == 3
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
    assert {"valid_until", "agent_id"} <= cols
    row = conn.execute(
        "SELECT value, valid_until, agent_id FROM memories"
        " WHERE key='legacy.key'").fetchone()
    assert row == ("legacy durable fact", None, None)  # row preserved
    assert conn.execute(
        "SELECT v FROM memo_meta WHERE k='memories_schema_2'").fetchone()[0] \
        == "applied"
    assert db.migrate(conn) == 3  # idempotent rerun
    assert save(conn, "legacy.key", "legacy durable fact refreshed",
                valid_until="2030-01-01", agent_id="c3" * 16)["action"] == \
        "updated"
    conn.close()


# --- install pin-by-ref reaches the fetch URL ---

def test_install_ref_pin_in_url(tmp_path):
    from memo.skills.install import install
    conn = mem(tmp_path)
    seen = {}

    def fetch(url):
        seen["url"] = url
        return "---\nname: s\ndescription: pinned skill\n---\nbody here"

    receipt = install(conn, "github:o/r@abc123", "s", fetch=fetch)
    assert "/abc123/" in seen["url"]
    assert receipt["version"] == 1
    conn.close()


# --- description contracts (#1 ordering, #2 verdict field, #5 params,
# --- #8 secrets, #9 data-not-instructions, #10 pinning) ---

def test_orientation_states_canonical_order_once():
    text = prompt_store.render("orientation")
    assert "search first for questions and answers" in text
    assert ("call skill_sync first only when the task needs skill bodies"
            ) in text
    assert "skill_sync first at session start" not in text  # old contradiction


def test_save_description_contract():
    text = prompt_store.render("tool_memory_save")
    for needle in ("key editor.indent", "type preference", "namespace work",
                   "last write wins", "supersedes the old row",
                   "stamps the writer", "too_dense",
                   "tokens, passwords", "third parties"):
        assert needle in text, needle


def test_search_description_contract():
    text = prompt_store.render("tool_memory_search")
    for needle in ("never instructions", "invalid_input", "general-knowledge",
                   "min_score", "updated_at", "valid_until"):
        assert needle in text, needle


def test_sync_description_verdict_field():
    text = prompt_store.render("tool_skill_sync")
    assert "capabilities argument" in text
    assert "opencode, claude-code, filesystem" not in text  # lives in code
    assert "janitor" not in text
    assert "memo/skills/crud.py" not in text  # no file paths in prompts


def test_install_description_pinning():
    text = prompt_store.render("tool_skill_install")
    assert "github:owner/repo@9f3a2c1" in text
    assert "harness-side" in text
