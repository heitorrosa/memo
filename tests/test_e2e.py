"""Task 8 e2e: plugin round-trips + enforcement texts (spec §§25-26, T-1..T-8).

Runs against HTTP (the deployed path), not just in-process calls.
TDD RED: opencode-memo/* does not exist yet; server pieces assumed green.
"""
import threading

import pytest

from memo import db, embeddings, summary
from memo.memory.save import save
from memo.server.cli import health_probe, main
from memo.server.core import build_server
from memo.server.http import build_http_app
from memo.skills.crud import get, put
from memo.skills.sync import sync

# Non-empty persist declaration -> file delta; names never matched server-side.
CAN_PERSIST = ["persist"]


def mem(tmp_path, name="memo.db"):
    conn = db.connect(str(tmp_path / name))
    assert db.migrate(conn) == 3
    return conn


@pytest.fixture(autouse=True)
def fallback():
    embeddings.force_fallback = True
    try:
        yield
    finally:
        embeddings.force_fallback = False


def serve(db_path, port, token="t"):
    import time

    import httpx
    import uvicorn
    app = build_http_app(build_server(db_path), token)
    srv = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health",
                         timeout=1.0).status_code == 200:
                return srv, th
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("server did not start")


def seed(conn):
    facts = [
        ("editor.indent", "prefers tabs for the project editor"),
        ("project.stack", "Vite React Tailwind with Bun runtime"),
        ("standup.time", "team standup every weekday at nine thirty"),
        ("deploy.cmd", "legacy deploy command lives in ops runbook"),
        ("review.cycle", "architecture review happens each quarter"),
    ]
    for k, v in facts:
        save(conn, k, v)
    put(conn, "qol-deploy", "Ship the Vite app: build, preview-check, publish.",
               "# QoL deploy\nrun build, preview, publish.")
    put(conn, "pytest-fast", "Run the repo test subset in under 60s.",
               "# pytest fast\nrun the subset.")


def test_cold_session_injects(tmp_path):
    """T-1: cold open returns summary with memories + L0 + stats; 2nd sync empty."""
    conn = mem(tmp_path)
    seed(conn)
    text = summary.bootstrap_text(conn)
    assert "Top memories (5)" in text
    assert "qol-deploy" in text and "pytest-fast" in text
    assert "Stats: 5/250 memories · 2 skills · db v3" in text
    d1 = sync(conn, "opencode", capabilities=CAN_PERSIST)
    assert d1["mode"] == "files" and len(d1["files_to_write"]) == 2
    known = [{"name": w["path"].split("/")[1], "hash": w["hash"]}
             for w in d1["files_to_write"]]
    d2 = sync(conn, "opencode", capabilities=CAN_PERSIST, known=known)
    assert d2["files_to_write"] == [] and d2["files_to_delete"] == []
    conn.close()


def test_skill_end_to_end(tmp_path):
    """T-3: put -> delta materializes file -> get returns the runnable body."""
    conn = mem(tmp_path)
    r = put(conn, "qol-deploy", "Ship the Vite app: build, preview-check, publish.",
                   "# QoL deploy\nrun build, preview, publish.")
    assert r["version"] == 1
    # after-hook equal-body no-op converges silently
    r2 = put(conn, "qol-deploy", "Ship the Vite app: build, preview-check, publish.",
                    "# QoL deploy\nrun build, preview, publish.")
    assert r2 == r
    d = sync(conn, "opencode", capabilities=CAN_PERSIST)
    assert [w["path"] for w in d["files_to_write"]] == ["skills/qol-deploy/SKILL.md"]
    body = get(conn, "qol-deploy")["body"]
    assert "preview" in body
    web = sync(conn, "web")
    assert "qol-deploy" in web["bootstrap_md"]  # bundle reflects it next call
    conn.close()


def test_web_bundle_reflects_skill(tmp_path):
    conn = mem(tmp_path)
    put(conn, "enem-grade", "Grade ENEM essays per INEP rubric, 5x200.",
               "# enem grade\nrubric body")
    b = sync(conn, "web")
    assert b["mode"] == "bundle" and b["truncated"] is False
    assert "enem-grade" in b["bootstrap_md"]
    conn.close()


def test_single_truth_direction(tmp_path):
    """Row beats file, always: stale client content never overwrites the row."""
    conn = mem(tmp_path)
    put(conn, "qol-deploy", "Ship desc.", "canonical row body v1")
    stale_known = [{"name": "qol-deploy", "hash": "0" * 64}]
    d = sync(conn, "opencode", capabilities=CAN_PERSIST, known=stale_known)
    assert len(d["files_to_write"]) == 1
    assert "canonical row body v1" in d["files_to_write"][0]["content"]
    assert get(conn, "qol-deploy")["body"] == "canonical row body v1"
    conn.close()


def test_session_survives_dead_db(tmp_path, capsys):
    """F-03/M-026: dead container -> warn + stale, session never raises."""
    assert health_probe("http://127.0.0.1:9/health") == 1
    assert "FAIL" in capsys.readouterr().err
    conn = mem(tmp_path)  # stale local snapshot still renders
    assert "(none yet)" in summary.bootstrap_text(conn)
    assert main(["--db", str(tmp_path / "m.db"), "--dump-tools"]) == 0
    conn.close()


def test_http_skill_sync_roundtrip(tmp_path):
    """Deployed-path proof: skill_sync over real HTTP through bearer auth."""
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    dbp = str(tmp_path / "m.db")
    conn = mem(tmp_path, "m.db")
    put(conn, "qol-deploy", "Ship desc.", "body here")
    conn.close()

    async def _sync():
        async with streamablehttp_client(
                "http://127.0.0.1:18091/mcp",
                headers={"Authorization": "Bearer t"}) as (rd, wr, _):
            async with ClientSession(rd, wr) as sess:
                await sess.initialize()
                return await sess.call_tool(
                    "skill_sync", {"harness": "opencode",
                                   "capabilities": CAN_PERSIST})

    srv, th = serve(dbp, 18091)
    try:
        res = asyncio.run(_sync())
        assert "qol-deploy" in res.content[0].text
    finally:
        srv.should_exit = True
        th.join(timeout=10)
