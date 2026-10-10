"""Server-side agent identity + sync state (Agent/AgentState).

The server mints and tracks an agent id so a harness may carry only the id
instead of a full known[] list; an id-only caller gets a precise delta from
the stored per-skill snapshot, plus server-computed moved-since-last-sync
flags. Legacy no-id calls keep today's routing (bundle by default).
"""

from memo import db
from memo import prompts as prompt_store
from memo.models.skills import utc_now
from memo.skills.crud import put, tombstone
from memo.skills.sync import sync

CAN_PERSIST = ["persist"]


def mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def put_skill(conn, name, body="body", desc=None):
    return put(conn, name, desc or f"{name} does things.", body)


def files_delta(conn, **kw):
    return sync(conn, "opencode", capabilities=CAN_PERSIST, **kw)


def yesterday():
    import datetime

    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def backdate(conn, agent_id, stamp=None):
    conn.execute("UPDATE agents SET last_seen=? WHERE agent_id=?",
                 (stamp or yesterday(), agent_id))
    conn.commit()


def test_mint_on_absent_returns_id(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "qol-deploy")
    bundle = sync(conn, "web")
    files = files_delta(conn)
    for out in (bundle, files):
        assert isinstance(out["agent_id"], str) and len(out["agent_id"]) == 32
    assert bundle["agent_id"] != files["agent_id"]  # each cold caller is new
    assert conn.execute("SELECT count(*) FROM agents").fetchone()[0] == 2
    conn.close()


def test_known_id_touches_and_persists_snapshot(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "a", body="v1")
    r1 = files_delta(conn)
    aid = r1["agent_id"]
    row = conn.execute("SELECT harness, last_seen FROM agents WHERE agent_id=?",
                       (aid,)).fetchone()
    assert row[0] == "opencode" and row[1]
    state = {r[0] for r in conn.execute(
        "SELECT skill FROM agent_state WHERE agent_id=?", (aid,))}
    assert state == {"a"}
    conn.close()


def test_id_only_delta_is_precise_not_baseline(tmp_path):
    # Second sync carries only the id: the server fills known[] from its
    # stored snapshot, so only the changed skill is re-sent.
    conn = mem(tmp_path)
    put_skill(conn, "a", body="v1")
    put_skill(conn, "b", body="v1")
    r1 = files_delta(conn)
    aid = r1["agent_id"]
    assert len(r1["files_to_write"]) == 2
    put_skill(conn, "b", body="v2")
    r2 = files_delta(conn, agent_id=aid)
    assert [w["path"] for w in r2["files_to_write"]] == ["skills/b/SKILL.md"]
    assert r2["agent_id"] == aid
    conn.close()


def test_id_only_echoes_full_known_also_accepted(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "a", body="v1")
    aid = files_delta(conn)["agent_id"]
    # Both forms accepted: explicit known[] wins over the stored snapshot.
    out = files_delta(conn, agent_id=aid, known=[{"name": "a", "hash": "0" * 64}])
    assert [w["path"] for w in out["files_to_write"]] == ["skills/a/SKILL.md"]
    conn.close()


def test_id_only_delete_surfaces_from_snapshot(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "gone")
    aid = files_delta(conn)["agent_id"]
    tombstone(conn, "gone")
    out = files_delta(conn, agent_id=aid)
    assert [d["path"] for d in out["files_to_delete"]] == ["skills/gone/SKILL.md"]
    conn.close()


def test_moved_since_last_sync_annotation(tmp_path):
    # Another actor mutates a skill after this agent's previous sync; the
    # delta entry and the top-level list flag it.
    conn = mem(tmp_path)
    r1 = files_delta(conn)
    aid = r1["agent_id"]
    backdate(conn, aid)
    put_skill(conn, "fresh", body="x")  # a different actor moves it
    r2 = files_delta(conn, agent_id=aid)
    writes = {w["path"]: w for w in r2["files_to_write"]}
    assert writes["skills/fresh/SKILL.md"]["moved_since_last_sync"] is True
    assert r2["moved_since_last_sync"] == ["fresh"]
    conn.close()


def test_moved_flag_absent_for_untouched(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "stable", body="s")
    aid = files_delta(conn)["agent_id"]
    backdate(conn, aid, utc_now())  # nothing touched since that sync
    r = files_delta(conn, agent_id=aid)
    assert r["files_to_write"] == []  # unchanged -> no delta at all
    assert r["moved_since_last_sync"] == []
    conn.close()


def test_moved_flag_in_bundle_mode(tmp_path):
    conn = mem(tmp_path)
    aid = sync(conn, "web")["agent_id"]
    backdate(conn, aid)
    put_skill(conn, "wskill")
    out = sync(conn, "web", agent_id=aid)
    assert out["mode"] == "bundle"
    assert out["moved_since_last_sync"] == ["wskill"]
    conn.close()


def test_unknown_id_re_mints_and_baselines(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "a")
    bogus = "deadbeef" * 4
    out = files_delta(conn, agent_id=bogus)
    assert out["agent_id"] != bogus and len(out["agent_id"]) == 32
    assert conn.execute("SELECT count(*) FROM agents WHERE agent_id=?",
                        (bogus,)).fetchone()[0] == 0
    # Re-mint is a fresh baseline: full materialization, no moved flags.
    assert len(out["files_to_write"]) == 1
    assert "moved_since_last_sync" not in out
    # Empty/None ids mint too.
    assert len(sync(conn, "web", agent_id="")["agent_id"]) == 32
    conn.close()


def test_stale_agents_pruned_on_any_sync(tmp_path):
    conn = mem(tmp_path)
    conn.execute(
        "INSERT INTO agents(agent_id,harness,capabilities,first_seen,last_seen)"
        " VALUES('stale','web','[]','2000-01-01T00:00:00Z','2000-01-01T00:00:00Z')")
    conn.execute(
        "INSERT INTO agent_state(agent_id,skill,hash,version,updated_at)"
        " VALUES('stale','s','h',1,'2000-01-01T00:00:00Z')")
    conn.execute(
        "INSERT INTO agents(agent_id,harness,capabilities,first_seen,last_seen)"
        " VALUES('fresh','web','[]',?,?)", (utc_now(), utc_now()))
    conn.commit()
    sync(conn, "web")  # any sync sweeps
    assert conn.execute("SELECT count(*) FROM agents WHERE agent_id='stale'"
                        ).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM agent_state WHERE agent_id='stale'"
                        ).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM agents WHERE agent_id='fresh'"
                        ).fetchone()[0] == 1  # untouched-but-recent survives
    conn.close()


def test_legacy_no_id_unchanged(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "qol-deploy", desc="Ship the Vite app.")
    bundle = sync(conn, "carrier-pigeon")
    assert bundle["mode"] == "bundle" and bundle["total"] == 1
    assert "qol-deploy" in bundle["bootstrap_md"]
    assert "moved_since_last_sync" not in bundle  # legacy shape + agent_id only
    files = sync(conn, "opencode", capabilities=CAN_PERSIST)
    assert files["mode"] == "files" and files["total"] == 1
    assert "moved_since_last_sync" not in files
    assert sync(conn, "")["error"] == "not_found"  # malformed still errors
    conn.close()


def test_schema_and_migration_key(tmp_path):
    conn = mem(tmp_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')")}
    assert {"agents", "agent_state"} <= tables
    assert conn.execute("SELECT v FROM memo_meta WHERE k='agents_schema_1'"
                        ).fetchone()[0] == "applied"
    assert {r[1] for r in conn.execute("PRAGMA table_info(agents)")} == {
        "agent_id", "harness", "capabilities", "first_seen", "last_seen"}
    assert {r[1] for r in conn.execute("PRAGMA table_info(agent_state)")} == {
        "agent_id", "skill", "hash", "version", "updated_at"}
    conn.close()


def test_prompt_documents_agent_identity():
    text = prompt_store.render("tool_skill_sync")
    for needle in (
        "Agent identity",
        "agent_id",
        "MINTS",
        "snapshot file",
        "conversation",
        "re-minted",
        "baseline",
        "known[]",
        "moved_since_last_sync",
    ):
        assert needle in text, f"missing agent-identity instruction: {needle!r}"


# --- MCP surface: agent_id reachable through the registered tool ---

def make_server(tmp_path):
    """Build the real MCP server over a tmp DB (same wiring a harness hits)."""
    from memo.server.core import build_server

    return build_server(str(tmp_path / "memo.db"))


def call(server, **args):
    """Invoke the registered skill_sync tool and decode its JSON envelope."""
    import asyncio
    import json

    res = asyncio.run(server.call_tool("skill_sync", args))
    return json.loads(res[0].text)


def test_mcp_schema_exposes_agent_id(tmp_path):
    import asyncio

    server = make_server(tmp_path)
    tool = next(t for t in asyncio.run(server.list_tools())
                if t.name == "skill_sync")
    assert "agent_id" in tool.inputSchema["properties"]  # reachable, not just named
    server.memo_conn.close()


def test_mcp_no_id_mints_and_returns(tmp_path):
    server = make_server(tmp_path)
    put_skill(server.memo_conn, "qol-deploy")
    out = call(server, harness="opencode", capabilities=["persist"])
    assert out["mode"] == "files"
    assert isinstance(out["agent_id"], str) and len(out["agent_id"]) == 32
    server.memo_conn.close()


def test_mcp_id_only_delta_is_precise(tmp_path):
    # Second MCP call carries ONLY the minted id: the server fills known[]
    # from its stored snapshot, so only the changed skill comes back.
    server = make_server(tmp_path)
    put_skill(server.memo_conn, "a", body="v1")
    put_skill(server.memo_conn, "b", body="v1")
    first = call(server, harness="opencode", capabilities=["persist"])
    aid = first["agent_id"]
    assert len(first["files_to_write"]) == 2  # cold: full materialization
    put_skill(server.memo_conn, "b", body="v2")
    second = call(server, harness="opencode", capabilities=["persist"],
                  agent_id=aid)
    assert [w["path"] for w in second["files_to_write"]] == ["skills/b/SKILL.md"]
    assert second["agent_id"] == aid
    server.memo_conn.close()


def test_mcp_unknown_id_re_mints_never_errors(tmp_path):
    server = make_server(tmp_path)
    put_skill(server.memo_conn, "a")
    bogus = "deadbeef" * 4
    out = call(server, harness="opencode", capabilities=["persist"],
               agent_id=bogus)
    assert "error" not in out
    assert out["agent_id"] != bogus and len(out["agent_id"]) == 32
    # Re-mint is a fresh baseline: full materialization, no moved flags.
    assert len(out["files_to_write"]) == 1
    assert "moved_since_last_sync" not in out
    server.memo_conn.close()
