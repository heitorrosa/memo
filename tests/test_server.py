import pytest

from memo import db, embeddings


def mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


@pytest.fixture(autouse=True)
def fallback():
    embeddings.force_fallback = True
    try:
        yield
    finally:
        embeddings.force_fallback = False


def test_envelopes(tmp_path):
    from memo.server.operations import (
        do_memory_delete,
        do_memory_save,
        do_memory_search,
        do_skill_get,
        do_skill_install,
        do_skill_sync,
    )
    conn = mem(tmp_path)
    assert do_memory_search(conn, "   ")["error"] == "empty_query"
    assert do_memory_delete(conn, "ghost") == {
        "key": "ghost", "action": "not_found"}
    assert do_skill_get(conn, "nope")["error"] == "not_found"
    assert do_skill_sync(conn, "")["error"] == "not_found"
    assert do_skill_install(conn, "bitbucket:x", "s")["error"] == "not_found"
    bad = do_skill_install(
        conn, "github:o/r", "s", fetch=lambda url: (_ for _ in ()).throw(
            RuntimeError("fetch failed: HTTP 404 for x")))
    assert bad == {"error": "internal", "detail": "fetch failed: HTTP 404 for x"}
    assert do_memory_save(conn, "k", "durable value here", typ="nonsense")["error"] == "invalid_input"
    conn.close()


def test_disable_flags(tmp_path, monkeypatch):
    from memo.server.core import build_server
    conn = mem(tmp_path)
    monkeypatch.setenv("MEMO_DISABLE_MEMORY", "1")
    s = build_server(str(tmp_path / "memo.db"))
    import asyncio
    names = sorted(t.name for t in asyncio.run(s.list_tools()))
    assert names == ["skill_get", "skill_install", "skill_put",
                     "skill_sync", "skill_update"]
    monkeypatch.setenv("MEMO_DISABLE_SKILLS", "yes")
    with pytest.raises(SystemExit) as e:
        build_server(str(tmp_path / "memo.db"))
    assert e.value.code == 2
    conn.close()


def test_dump_tools(tmp_path, capsys):
    from memo.server.cli import main
    assert main(["--db", str(tmp_path / "m.db"), "--dump-tools"]) == 0
    names = capsys.readouterr().out.split()
    assert names == ["memory_delete", "memory_save", "memory_search",
                     "skill_get", "skill_install", "skill_put",
                     "skill_sync", "skill_update"]


def test_startup_line():
    from memo.server.shared import startup_line
    assert startup_line("x.db", True, True, True) == \
        "memo v3 db=x.db vec=vec memory=on skills=on"
    assert startup_line("x.db", False, False, True) == \
        "memo v3 db=x.db vec=bruteforce memory=off skills=on"


def test_vec_fallback(tmp_path):
    from memo.memory.save import save
    from memo.memory.search import search
    conn = mem(tmp_path)
    save(conn, "editor.indent", "prefers tabs for the project")
    hits = search(conn, "indentation")
    assert hits and hits[0]["key"] == "editor.indent"
    conn.close()


def test_http_no_token_refuses(tmp_path, monkeypatch, capsys):
    from memo.server.cli import main_http
    monkeypatch.delenv("MEMO_TOKEN", raising=False)
    assert main_http(["--db", str(tmp_path / "m.db")]) == 2
    assert "MEMO_TOKEN required" in capsys.readouterr().err


def serve(db_path, port, token="t", log_level="error", access_log=False):
    import threading
    import time
    import httpx
    import uvicorn
    from memo.server.core import build_server
    from memo.server.http import build_http_app
    app = build_http_app(build_server(db_path), token)
    srv = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level=log_level,
        access_log=access_log))
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


def test_health(tmp_path):
    import httpx
    srv, th = serve(str(tmp_path / "m.db"), 18081)
    try:
        r = httpx.get("http://127.0.0.1:18081/health", timeout=5.0)
        assert r.status_code == 200
        assert r.json() == {"ok": True, "version": 3, "vec": "bruteforce"}
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_http_tightened_host_rejected(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setenv("MEMO_TRUSTED_HOSTS", "memo.tunnel.example")
    srv, th = serve(str(tmp_path / "m.db"), 18095)
    try:
        r = httpx.post("http://127.0.0.1:18095/mcp", json=init_body(),
                       headers={**JSON_ONLY, "Authorization": "Bearer t",
                                "Host": "evil.example"},
                       timeout=5.0)
        assert r.status_code == 421
        assert "Invalid Host header" in r.text
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_http_trusted_host_from_env_passes(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setenv("MEMO_TRUSTED_HOSTS", "memo.tunnel.example")
    srv, th = serve(str(tmp_path / "m.db"), 18096)
    try:
        r = httpx.post("http://127.0.0.1:18096/mcp", json=init_body(),
                       headers={**JSON_ONLY, "Authorization": "Bearer t",
                                "Host": "memo.tunnel.example"},
                       timeout=5.0)
        assert r.status_code == 200, r.text
        assert r.json()["result"]["serverInfo"]["name"] == "memo"
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_http_default_hosts_permissive(tmp_path, monkeypatch):
    import httpx
    monkeypatch.delenv("MEMO_TRUSTED_HOSTS", raising=False)
    srv, th = serve(str(tmp_path / "m.db"), 18097)
    try:
        ok = httpx.post("http://127.0.0.1:18097/mcp", json=init_body(),
                        headers={**JSON_ONLY, "Authorization": "Bearer t"},
                        timeout=5.0)
        assert ok.status_code == 200, ok.text
        tunnel = httpx.post("http://127.0.0.1:18097/mcp", json=init_body(),
                            headers={**JSON_ONLY, "Authorization": "Bearer t",
                                     "Host": "random-1234.trycloudflare.com"},
                            timeout=5.0)
        assert tunnel.status_code == 200, tunnel.text
        assert tunnel.json()["result"]["serverInfo"]["name"] == "memo"
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_http_default_origin_still_gated(tmp_path, monkeypatch):
    import httpx
    monkeypatch.delenv("MEMO_TRUSTED_HOSTS", raising=False)
    srv, th = serve(str(tmp_path / "m.db"), 18098)
    try:
        r = httpx.post("http://127.0.0.1:18098/mcp", json=init_body(),
                       headers={**JSON_ONLY, "Authorization": "Bearer t",
                                "Origin": "https://evil.example"},
                       timeout=5.0)
        assert r.status_code == 403
        assert "Invalid Origin header" in r.text
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_http_auth_reject(tmp_path):
    import httpx
    srv, th = serve(str(tmp_path / "m.db"), 18082)
    try:
        base = "http://127.0.0.1:18082/mcp"
        assert httpx.post(base, json={}, timeout=5.0).status_code == 401
        assert httpx.post(base, json={},
                          headers={"Authorization": "Bearer wrong"},
                          timeout=5.0).status_code == 401
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_http_query_token_accepted(tmp_path):
    import httpx
    srv, th = serve(str(tmp_path / "m.db"), 18086)
    try:
        r = httpx.post("http://127.0.0.1:18086/mcp?access_token=t",
                       json={}, timeout=5.0)
        assert r.status_code != 401
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_http_query_token_wrong_rejected(tmp_path):
    import httpx
    srv, th = serve(str(tmp_path / "m.db"), 18087)
    try:
        r = httpx.post("http://127.0.0.1:18087/mcp?access_token=wrong",
                       json={}, timeout=5.0)
        assert r.status_code == 401
        assert r.json() == {"error": "unauthorized"}
        assert "wrong" not in r.text
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_http_header_wins_over_query(tmp_path):
    import httpx
    srv, th = serve(str(tmp_path / "m.db"), 18088)
    try:
        base = "http://127.0.0.1:18088/mcp"
        assert httpx.post(base + "?access_token=t", json={},
                          headers={"Authorization": "Bearer wrong"},
                          timeout=5.0).status_code == 401
        assert httpx.post(base + "?access_token=wrong", json={},
                          headers={"Authorization": "Bearer t"},
                          timeout=5.0).status_code != 401
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_query_token_never_logged(tmp_path, capsys):
    import httpx
    secret = "supersecret-query-token-xyz"
    srv, th = serve(str(tmp_path / "m.db"), 18089, token=secret,
                    log_level="info", access_log=True)
    try:
        httpx.post(f"http://127.0.0.1:18089/mcp?access_token={secret}",
                   json={}, timeout=5.0)
    finally:
        srv.should_exit = True
        th.join(timeout=10)
    assert secret in capsys.readouterr().out
    srv, th = serve(str(tmp_path / "m.db"), 18090, token=secret,
                    log_level="info", access_log=False)
    try:
        httpx.post(f"http://127.0.0.1:18090/mcp?access_token={secret}",
                   json={}, timeout=5.0)
    finally:
        srv.should_exit = True
        th.join(timeout=10)
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


JSON_ONLY = {"Accept": "application/json", "Content-Type": "application/json"}
BOTH = {"Accept": "application/json, text/event-stream",
        "Content-Type": "application/json"}
SSE_ONLY = {"Accept": "text/event-stream", "Content-Type": "application/json"}


def init_body(pid=1):
    return {"jsonrpc": "2.0", "id": pid, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "chatgpt", "version": "1"}}}


def test_json_only_initialize_and_tools_list(tmp_path):
    import httpx
    srv, th = serve(str(tmp_path / "m.db"), 18091)
    try:
        base = "http://127.0.0.1:18091/mcp"
        h = {**JSON_ONLY, "Authorization": "Bearer t"}
        r = httpx.post(base, json=init_body(), headers=h, timeout=5.0)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["result"]["serverInfo"]["name"] == "memo"
        sid = r.headers["mcp-session-id"]

        h2 = {**h, "mcp-session-id": sid, "mcp-protocol-version": "2025-06-18"}
        n = httpx.post(base, json={"jsonrpc": "2.0",
                                   "method": "notifications/initialized"},
                       headers=h2, timeout=5.0)
        assert n.status_code == 202, n.text
        assert n.headers["content-type"].startswith("application/json")

        tl = httpx.post(base, json={"jsonrpc": "2.0", "id": 2,
                                    "method": "tools/list", "params": {}},
                        headers=h2, timeout=5.0)
        assert tl.status_code == 200, tl.text
        assert tl.headers["content-type"].startswith("application/json")
        names = sorted(t["name"] for t in tl.json()["result"]["tools"])
        assert "memory_save" in names and "skill_sync" in names
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_both_accept_still_event_stream(tmp_path):
    import httpx
    srv, th = serve(str(tmp_path / "m.db"), 18092)
    try:
        r = httpx.post("http://127.0.0.1:18092/mcp", json=init_body(),
                       headers={**BOTH, "Authorization": "Bearer t"},
                       timeout=5.0)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        assert '"result"' in r.text
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_sse_only_unchanged_406(tmp_path):
    import httpx
    srv, th = serve(str(tmp_path / "m.db"), 18093)
    try:
        r = httpx.post("http://127.0.0.1:18093/mcp", json=init_body(),
                       headers={**SSE_ONLY, "Authorization": "Bearer t"},
                       timeout=5.0)
        assert r.status_code == 406
        assert "must accept both" in r.text
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_negotiation_auth_still_enforced(tmp_path):
    import httpx
    srv, th = serve(str(tmp_path / "m.db"), 18094)
    try:
        base = "http://127.0.0.1:18094/mcp"
        assert httpx.post(base, json=init_body(), headers=JSON_ONLY,
                          timeout=5.0).status_code == 401
        ok = httpx.post(base, json=init_body(),
                        headers={**JSON_ONLY, "Authorization": "Bearer t"},
                        timeout=5.0)
        assert ok.status_code == 200, ok.text
        q = httpx.post(base + "?access_token=t", json=init_body(),
                       headers=JSON_ONLY, timeout=5.0)
        assert q.status_code == 200, q.text
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def test_http_tool_parity(tmp_path, capsys):
    import asyncio
    from memo.server.cli import main
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    dbp = str(tmp_path / "m.db")
    assert main(["--db", dbp, "--dump-tools"]) == 0
    dumped = sorted(capsys.readouterr().out.split())

    async def names():
        async with streamablehttp_client(
                "http://127.0.0.1:18083/mcp",
                headers={"Authorization": "Bearer t"}) as (rd, wr, _):
            async with ClientSession(rd, wr) as sess:
                await sess.initialize()
                tools = await sess.list_tools()
                return sorted(t.name for t in tools.tools)
    srv, th = serve(dbp, 18083)
    try:
        assert asyncio.run(names()) == dumped
    finally:
        srv.should_exit = True
        th.join(timeout=10)
