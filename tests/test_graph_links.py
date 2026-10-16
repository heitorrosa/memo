import pytest

from memo import db, embeddings
from memo import prompts as prompt_store
from memo.memory.graph import MAX_ALIASES, MAX_LINKS
from memo.memory.save import fallback_agent_id, save


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


def edge(conn, src, dst, rel):
    return conn.execute(
        "SELECT weight FROM edges WHERE src_key=? AND dst_key=? AND rel=?",
        (src, dst, rel)).fetchone()


def declarers(conn, src, dst, rel):
    return conn.execute(
        "SELECT declarers, status, evidence FROM edge_declarers"
        " WHERE src_key=? AND dst_key=? AND rel=?", (src, dst, rel)).fetchone()


def alias_row(conn, segment, canonical):
    return conn.execute(
        "SELECT declarers, status, evidence FROM aliases"
        " WHERE segment=? AND canonical=?", (segment, canonical)).fetchone()


def seed(conn, key, value=None):
    assert save(conn, key, value or f"durable atlas note for {key}")["action"] == "created"


def test_migration_creates_alias_tables(tmp_path):
    conn = mem(tmp_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"aliases", "edge_declarers"} <= tables
    assert conn.execute("SELECT v FROM memo_meta WHERE k='memories_schema_4'"
                        ).fetchone()[0] == "applied"
    assert {r[1] for r in conn.execute("PRAGMA table_info(aliases)")} == {
        "segment", "canonical", "namespace", "declarers", "status", "evidence",
        "first_seen", "last_seen"}
    info = conn.execute("PRAGMA table_info(edge_declarers)").fetchall()
    assert {r[1] for r in info} == {
        "src_key", "dst_key", "rel", "namespace", "declarers", "status",
        "evidence", "first_seen", "last_seen"}
    assert [r[1] for r in info if r[5]] == [
        "src_key", "dst_key", "rel", "namespace"]
    conn.close()


def test_valid_links_stored_with_agreed_weight(tmp_path):
    conn = mem(tmp_path)
    seed(conn, "graph.alpha")
    r = save(conn, "graph.beta", "durable atlas note for graph beta",
             links=[{"to": "graph.alpha", "rel": "supports"}], agent_id="A" * 8)
    assert r["action"] == "created" and "suggestion" not in r
    assert edge(conn, "graph.beta", "graph.alpha", "supports") == (0.4,)
    conn.close()


def test_bad_rel_rejected_but_save_lands(tmp_path):
    conn = mem(tmp_path)
    seed(conn, "graph.alpha")
    r = save(conn, "graph.beta", "durable atlas note for graph beta",
             links=[{"to": "graph.alpha", "rel": "nope"}])
    assert r["action"] == "created"
    assert "bad rel" in r["suggestion"]
    assert edge(conn, "graph.beta", "graph.alpha", "nope") is None
    conn.close()


def test_versioning_rel_rejected(tmp_path):
    conn = mem(tmp_path)
    seed(conn, "graph.alpha")
    r = save(conn, "graph.beta", "durable atlas note for graph beta",
             links=[{"to": "graph.alpha", "rel": "supersedes"}])
    assert r["action"] == "created"
    assert "versioning" in r["suggestion"]
    assert edge(conn, "graph.beta", "graph.alpha", "supersedes") is None
    conn.close()


def test_missing_target_rejected(tmp_path):
    conn = mem(tmp_path)
    r = save(conn, "graph.beta", "durable atlas note for graph beta",
             links=[{"to": "ghost.node", "rel": "supports"}])
    assert r["action"] == "created"
    assert "does not exist" in r["suggestion"]
    assert edge(conn, "graph.beta", "ghost.node", "supports") is None
    conn.close()


def test_self_link_rejected(tmp_path):
    conn = mem(tmp_path)
    r = save(conn, "graph.beta", "durable atlas note for graph beta",
             links=[{"to": "graph.beta", "rel": "mentions"}])
    assert r["action"] == "created"
    assert "self-link" in r["suggestion"]
    conn.close()


def test_virtual_entity_node_is_a_valid_target(tmp_path):
    conn = mem(tmp_path)
    seed(conn, "graph.alpha")
    r = save(conn, "graph.beta", "durable atlas note for graph beta",
             links=[{"to": "graph", "rel": "supports"}], agent_id="A" * 8)
    assert "suggestion" not in r
    assert edge(conn, "graph.beta", "graph", "supports") == (0.4,)
    conn.close()


def test_links_cap_enforced(tmp_path):
    conn = mem(tmp_path)
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
             "hotel", "india", "juliet", "kilo", "lima"]
    for name in names:
        seed(conn, f"cap.{name}")
    links = [{"to": f"cap.{name}", "rel": "supports"} for name in names]
    r = save(conn, "cap.src", "durable atlas note for cap source", links=links,
             agent_id="A" * 8)
    assert r["action"] == "created"
    assert f"over cap {MAX_LINKS} dropped" in r["suggestion"]
    stored = conn.execute(
        "SELECT count(*) FROM edges WHERE src_key='cap.src' AND rel='supports'"
    ).fetchone()[0]
    assert stored == MAX_LINKS
    conn.close()


def test_aliases_cap_enforced(tmp_path):
    conn = mem(tmp_path)
    aliases = [{"segment": "seg.one", "canonical": f"canon.{n}"}
               for n in ("alpha", "bravo", "charlie", "delta", "echo",
                         "foxtrot", "golf")]
    r = save(conn, "seg.one", "durable atlas note for segment one",
             aliases=aliases, agent_id="A" * 8)
    assert r["action"] == "created"
    assert f"over cap {MAX_ALIASES} dropped" in r["suggestion"]
    assert conn.execute("SELECT count(*) FROM aliases").fetchone()[0] == MAX_ALIASES
    conn.close()


def test_alias_candidate_then_confirmed_on_second_declarer(tmp_path):
    conn = mem(tmp_path)
    first = save(conn, "seg.one", "durable atlas note for segment one",
                 aliases=[{"segment": "seg.one", "canonical": "canon.one"}],
                 agent_id="A" * 8)
    assert "suggestion" not in first
    assert alias_row(conn, "seg.one", "canon.one") == ("AAAAAAAA", "candidate", 1)
    save(conn, "seg.one", "durable atlas note for segment one again",
         aliases=[{"segment": "seg.one", "canonical": "canon.one"}],
         agent_id="B" * 8)
    assert alias_row(conn, "seg.one", "canon.one") == (
        "AAAAAAAA,BBBBBBBB", "confirmed", 2)
    conn.close()


def test_same_agent_redeclare_stays_candidate(tmp_path):
    conn = mem(tmp_path)
    for _ in range(2):
        save(conn, "seg.one", "durable atlas note for segment one",
             aliases=[{"segment": "seg.one", "canonical": "canon.one"}],
             agent_id="A" * 8)
    assert alias_row(conn, "seg.one", "canon.one") == ("AAAAAAAA", "candidate", 2)
    conn.close()


def test_competing_canonicals_both_persist(tmp_path):
    conn = mem(tmp_path)
    save(conn, "seg.one", "durable atlas note for segment one",
         aliases=[{"segment": "seg.one", "canonical": "canon.one"},
                  {"segment": "seg.one", "canonical": "canon.two"}],
         agent_id="A" * 8)
    rows = conn.execute(
        "SELECT canonical FROM aliases WHERE segment='seg.one' ORDER BY 1"
    ).fetchall()
    assert rows == [("canon.one",), ("canon.two",)]
    conn.close()


def test_fallback_id_stable_per_token_ua_distinct_across_ua(tmp_path):
    one = fallback_agent_id("token-1", "ua-a")
    assert one == fallback_agent_id("token-1", "ua-a")
    assert one.startswith("auto-") and len(one) == 21
    assert one != fallback_agent_id("token-1", "ua-b")
    assert one != fallback_agent_id("token-2", "ua-a")
    assert fallback_agent_id(None, None) == fallback_agent_id("", "unknown")


def test_fallback_declarer_never_self_confirms(tmp_path):
    conn = mem(tmp_path)
    for _ in range(2):
        save(conn, "seg.one", "durable atlas note for segment one",
             aliases=[{"segment": "seg.one", "canonical": "canon.one"}],
             token="token-1", user_agent="ua-a")
    row = alias_row(conn, "seg.one", "canon.one")
    assert row[0] == fallback_agent_id("token-1", "ua-a")
    assert (row[1], row[2]) == ("candidate", 2)
    save(conn, "seg.one", "durable atlas note for segment one",
         aliases=[{"segment": "seg.one", "canonical": "canon.one"}],
         token="token-1", user_agent="ua-b")
    assert alias_row(conn, "seg.one", "canon.one")[1] == "confirmed"
    conn.close()


def test_weight_recomputed_on_agreement_upgrade(tmp_path):
    conn = mem(tmp_path)
    seed(conn, "graph.alpha")
    save(conn, "graph.beta", "durable atlas note for graph beta",
         links=[{"to": "graph.alpha", "rel": "supports"}], agent_id="A" * 8)
    assert edge(conn, "graph.beta", "graph.alpha", "supports") == (0.4,)
    assert declarers(conn, "graph.beta", "graph.alpha", "supports") == (
        "AAAAAAAA", "candidate", 1)
    save(conn, "graph.beta", "durable atlas note for graph beta",
         links=[{"to": "graph.alpha", "rel": "supports"}], agent_id="B" * 8)
    assert edge(conn, "graph.beta", "graph.alpha", "supports") == (0.8,)
    assert declarers(conn, "graph.beta", "graph.alpha", "supports") == (
        "AAAAAAAA,BBBBBBBB", "confirmed", 2)
    conn.close()


def test_contradicts_stored_weight_zero(tmp_path):
    conn = mem(tmp_path)
    seed(conn, "graph.alpha")
    for agent in ("A" * 8, "B" * 8):
        save(conn, "graph.beta", "durable atlas note for graph beta",
             links=[{"to": "graph.alpha", "rel": "contradicts"}], agent_id=agent)
    assert edge(conn, "graph.beta", "graph.alpha", "contradicts") == (0.0,)
    assert declarers(conn, "graph.beta", "graph.alpha", "contradicts")[1] == "confirmed"
    conn.close()


def test_same_as_link_records_alias_not_edge(tmp_path):
    conn = mem(tmp_path)
    seed(conn, "graph.alpha")
    save(conn, "graph.beta", "durable atlas note for graph beta",
         links=[{"to": "graph.alpha", "rel": "same_as"}], agent_id="A" * 8)
    assert edge(conn, "graph.beta", "graph.alpha", "same_as") is None
    assert alias_row(conn, "graph.beta", "graph.alpha") == (
        "AAAAAAAA", "candidate", 1)
    conn.close()


def test_auto_emission_unchanged_by_agent_declaration(tmp_path):
    conn = mem(tmp_path)
    save(conn, "graph.alpha", "durable atlas note for graph alpha",
         links=[{"to": "graph", "rel": "mentions"}], agent_id="A" * 8)
    assert edge(conn, "graph.alpha", "graph", "mentions") == (1.0,)
    assert declarers(conn, "graph.alpha", "graph", "mentions") is None
    conn.close()


def test_duplicate_link_idempotent(tmp_path):
    conn = mem(tmp_path)
    seed(conn, "graph.alpha")
    links = [{"to": "graph.alpha", "rel": "supports"},
             {"to": "graph.alpha", "rel": "supports"}]
    save(conn, "graph.beta", "durable atlas note for graph beta", links=links,
         agent_id="A" * 8)
    assert conn.execute(
        "SELECT count(*) FROM edges WHERE src_key='graph.beta'"
        " AND dst_key='graph.alpha'").fetchone()[0] == 1
    assert declarers(conn, "graph.beta", "graph.alpha", "supports")[2] == 1
    conn.close()


def test_operations_forwards_links_and_aliases(tmp_path):
    from memo.server.operations import do_memory_save
    conn = mem(tmp_path)
    seed(conn, "graph.alpha")
    r = do_memory_save(
        conn, "graph.beta", "durable atlas note for graph beta",
        links=[{"to": "graph.alpha", "rel": "derived_from"}],
        aliases=[{"segment": "graph.beta", "canonical": "graph.alpha"}],
        agent_id="A" * 8)
    assert r["action"] == "created"
    assert edge(conn, "graph.beta", "graph.alpha", "derived_from") == (0.5,)
    assert alias_row(conn, "graph.beta", "graph.alpha")[1] == "candidate"
    conn.close()


def test_description_declares_links_and_aliases():
    text = prompt_store.render("tool_memory_save")
    for needle in ("links is a list of {to, rel}", "aliases is a list of "
                   "{segment, canonical}", "10 links, 5 aliases",
                   "never blocks the save", "Declare only what you know"):
        assert needle in text, needle


def test_mcp_schema_exposes_links_and_aliases(tmp_path):
    import asyncio
    from memo.server.core import build_server
    server = build_server(str(tmp_path / "memo.db"))
    tool = next(t for t in asyncio.run(server.list_tools())
                if t.name == "memory_save")
    assert {"links", "aliases"} <= set(tool.inputSchema["properties"])
    server.memo_conn.close()


def test_mcp_memory_save_accepts_links(tmp_path):
    import asyncio
    import json
    from memo.server.core import build_server
    server = build_server(str(tmp_path / "memo.db"))
    conn = server.memo_conn
    seed(conn, "graph.alpha")
    res = asyncio.run(server.call_tool("memory_save", {
        "key": "graph.beta", "value": "durable atlas note for graph beta",
        "links": [{"to": "graph.alpha", "rel": "supports"}]}))
    out = json.loads(res[0].text)
    assert out["action"] == "created"
    assert edge(conn, "graph.beta", "graph.alpha", "supports") == (0.4,)
    conn.close()
