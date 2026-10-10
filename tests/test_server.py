"""Task 7 tests: maintenance.run + server wiring + envelopes (spec §§7,16-17,23-24).

TDD RED: maintenance.py / server.py do not exist yet.
"""
import datetime
import math

import pytest

from memo import db, embeddings

MISSING = object()


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


def ts(days_ago):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days_ago)).isoformat()


def insert(conn, key, *, typ="context", ns="default", age_days=10.0,
            uses=0, score=1.0, status="current", value=None):
    emb = embeddings.embed([(value or f"durable project fact {key}")])[0]
    stamp = ts(age_days)
    with conn:
        conn.execute(
            "INSERT INTO memories(key,namespace,value,type,embedding,stability,"
            "score,uses,created_at,updated_at,valid_from,status,family_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, ns, value or f"durable project fact {key} for the deploy pipeline",
             typ, emb, {"preference": 14.0, "feedback": 10.0,
                        "analysis": 7.0, "context": 3.0}[typ],
             score, uses, stamp, stamp, stamp, status, f"fam-{key}"))
    return key


def count(conn, ns="default", status="current"):
    return conn.execute(
        "SELECT COUNT(*) FROM memories WHERE namespace=? AND status=?",
        (ns, status)).fetchone()[0]


def expected_victim(conn, ns="default"):
    """Independent argmin E over current rows (reimplements §16 from raw SQL)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    best, best_e = MISSING, None
    for key, uses, stab, ca in conn.execute(
            "SELECT key, uses, stability, created_at FROM memories"
            " WHERE namespace=? AND status='current'", (ns,)):
        age = max(0.0, (now - datetime.datetime.fromisoformat(ca))
                  .total_seconds() / 86400)
        if age < 1.0:
            continue  # 24h grace
        e = (uses / max(age, 1.0)) * math.exp(-age / stab)
        if best_e is None or e < best_e:
            best, best_e = key, e
    assert best is not MISSING
    return best


def fill_250(conn):
    # 249 ordinary rows + 1 crafted minimal-E victim (a preference: M-016
    # lets the CAP move prefs even though the cron may not).
    for i in range(249):
        insert(conn, f"fillrow{i:03d}", age_days=2.0 + (i % 25),
               uses=1 + (i % 6))
    insert(conn, "old.deploy.cmd", typ="preference", age_days=120.0,
           uses=1, score=1.0,
           value="durable project fact: the legacy deploy command lives in ops docs")
    assert count(conn) == 250


# --- cap & eviction (§16) ---

def test_251st_evicts_one(tmp_path):
    from memo.memory.save import save
    conn = mem(tmp_path)
    fill_250(conn)
    expected = expected_victim(conn)
    assert expected == "old.deploy.cmd"
    r = save(conn, "zebrafinch", "durable project fact: the team writes Python services with uv")
    assert r["action"] == "created"
    assert r["archived"] == expected  # F-07: names the victim
    assert count(conn) == 250
    assert conn.execute(
        "SELECT archived_reason FROM archived_memories WHERE key=?",
        (expected,)).fetchone()[0] == "cap_eviction"
    assert conn.execute(
        "SELECT 1 FROM memories WHERE key='zebrafinch'").fetchone()
    conn.close()


def test_burst_grace(tmp_path):
    from memo.memory.save import save
    conn = mem(tmp_path)
    fill_250(conn)
    fresh = ["zebrafinch", "quixotic", "wamblegronk", "xyloid", "kryften",
             "vexillary", "plumbus", "zorgblatt", "quandle", "fimble"]
    for k in fresh:
        r = save(conn, k, f"durable project fact about {k} for the deploy pipeline")
        assert r["action"] == "created", k
    for k in fresh:  # T-4: newest 10 all present
        assert conn.execute(
            "SELECT 1 FROM memories WHERE key=?", (k,)).fetchone(), k
    victims = [x[0] for x in conn.execute(
        "SELECT key FROM archived_memories WHERE archived_reason='cap_eviction'")]
    assert len(victims) == 10
    now = datetime.datetime.now(datetime.timezone.utc)
    for v in victims:  # F-08: no victim < 1d old
        ca = conn.execute(
            "SELECT created_at FROM archived_memories WHERE key=?", (v,)).fetchone()[0]
        age = (now - datetime.datetime.fromisoformat(ca)).total_seconds() / 86400
        assert age >= 1.0, v
    conn.close()


def test_superseded_excluded_from_cap(tmp_path):
    from memo.memory.save import save
    conn = mem(tmp_path)
    fill_250(conn)
    for i in range(5):  # history rows: must not count, must not move (M-006)
        insert(conn, f"hist.old.{i}", status="superseded", age_days=50.0, uses=1)
    r = save(conn, "zebrafinch", "durable project fact: the team writes Python services with uv")
    assert r["action"] == "created"
    assert count(conn) == 250
    assert count(conn, status="superseded") == 5
    conn.close()


def test_eviction_bounded(tmp_path):
    from memo import maintenance
    conn = mem(tmp_path)
    fill_250(conn)
    for i in range(3):
        insert(conn, f"over{i}", age_days=20.0, uses=1)
    out = maintenance.run(conn, max_moves=2)
    assert len(out["archived"]) == 2
    assert count(conn) == 251  # 253 - 2: converges over calls, idempotent
    out2 = maintenance.run(conn)
    assert count(conn) == 250 and out2["evicted"] is not None
    conn.close()


# --- cron + rollup + tombstones (§17, §23) ---

def test_cron_triple_and(tmp_path):
    from memo import maintenance
    conn = mem(tmp_path)
    insert(conn, "cron.due", typ="analysis", age_days=100.0, uses=0, score=0.05,
           value="durable analysis conclusion from the old review cycle")
    out = maintenance.run(conn)
    assert "cron.due" in out["archived"]
    assert conn.execute(
        "SELECT archived_reason FROM archived_memories WHERE key='cron.due'"
    ).fetchone()[0] == "cron"
    conn.close()


@pytest.mark.parametrize("kw", [
    {"score": 0.5},   # boosted: score veto
    {"uses": 3},      # touched: uses veto
    {"age_days": 10.0},  # young: age veto
])
def test_cron_vetoes(tmp_path, kw):
    from memo import maintenance
    conn = mem(tmp_path)
    base = {"typ": "analysis", "age_days": 100.0, "uses": 0, "score": 0.05,
            "value": "durable analysis conclusion from the old review cycle"}
    base.update(kw)
    insert(conn, "cron.safe", **base)
    out = maintenance.run(conn)
    assert "cron.safe" not in out["archived"]
    assert conn.execute(
        "SELECT 1 FROM memories WHERE key='cron.safe'").fetchone()
    conn.close()


def test_pref_cron_exempt(tmp_path):
    from memo import maintenance
    conn = mem(tmp_path)
    for typ in ("preference", "feedback"):
        insert(conn, f"keep.{typ}", typ=typ, age_days=100.0, uses=0,
               score=0.05, value=f"durable {typ} fact kept forever by policy")
    out = maintenance.run(conn)
    assert out["archived"] == [] and out["evicted"] is None
    assert count(conn) == 2  # M-016: only cap eviction may move these
    conn.close()


def test_context_rollup(tmp_path):
    from memo import maintenance
    conn = mem(tmp_path)
    for i in range(3):
        insert(conn, f"ctx.old.{i}", typ="context", age_days=40.0,
               uses=1, score=1.0,
               value=f"durable context note number {i} about the deploy pipeline")
    out = maintenance.run(conn)
    assert out["rolled_up"] == 1
    ep = conn.execute(
        "SELECT summary, source_count, rolled_up_at, started_at, ended_at"
        " FROM episodes").fetchone()
    assert ep[1] == 3
    assert ep[0].startswith("auto-rollup of 3 context rows: ")
    assert ep[2] and ep[3] and ep[4] and ep[3] <= ep[4]
    assert count(conn) == 0  # N rows out, 1 episode elsewhere: N-1 slots back
    for i in range(3):
        assert conn.execute(
            "SELECT archived_reason FROM archived_memories WHERE key=?",
            (f"ctx.old.{i}",)).fetchone()[0] == "rollup"
    conn.close()


def test_tombstone_expiry(tmp_path):
    from memo import maintenance
    from memo.skills.crud import put, tombstone
    conn = mem(tmp_path)
    put(conn, "dead.skill", "old workflow", "body")
    tombstone(conn, "dead.skill")
    with conn:
        conn.execute("UPDATE skills SET updated_at=? WHERE name='dead.skill'",
                     (ts(31.0),))
    put(conn, "fresh.skill", "new workflow", "body")
    tombstone(conn, "fresh.skill")
    out = maintenance.run(conn)
    assert conn.execute(
        "SELECT 1 FROM skills WHERE name='dead.skill'").fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM skills WHERE name='fresh.skill'").fetchone()
    assert out["rolled_up"] == 0
    conn.close()


def test_save_then_sweep_guard(tmp_path):
    from memo.memory.save import save
    conn = mem(tmp_path)
    # A row saved this call is never swept by the same call's tail (M-024).
    r = save(conn, "fresh.note", "durable project fact: just learned this minute")
    assert r["action"] == "created"
    assert conn.execute(
        "SELECT 1 FROM memories WHERE key='fresh.note'").fetchone()
    conn.close()


# --- envelopes (§7): every tool error case returns, never raises ---

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
        "key": "ghost", "action": "not_found"}  # success-shaped, NOT error
    assert do_skill_get(conn, "nope")["error"] == "not_found"
    assert do_skill_sync(conn, "")["error"] == "not_found"
    assert do_skill_install(conn, "bitbucket:x", "s")["error"] == "not_found"
    bad = do_skill_install(
        conn, "github:o/r", "s", fetch=lambda url: (_ for _ in ()).throw(
            RuntimeError("fetch failed: HTTP 404 for x")))
    assert bad == {"error": "internal", "detail": "fetch failed: HTTP 404 for x"}
    assert do_memory_save(conn, "k", "durable value here", typ="nonsense")["error"] == "internal"
    conn.close()


# --- disable flags, dump-tools, startup line, vec fallback ---

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
    assert e.value.code == 2  # F-13: no domains enabled
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


# --- HTTP: token gate, auth, parity, health ---

def test_http_no_token_refuses(tmp_path, monkeypatch, capsys):
    from memo.server.cli import main_http
    monkeypatch.delenv("MEMO_TOKEN", raising=False)
    assert main_http(["--db", str(tmp_path / "m.db")]) == 2
    assert "MEMO_TOKEN required" in capsys.readouterr().err  # M-033


def serve(db_path, port, token="t"):
    import threading
    import time
    import httpx
    import uvicorn
    from memo.server.core import build_server
    from memo.server.http import build_http_app
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


def test_health(tmp_path):
    import httpx
    srv, th = serve(str(tmp_path / "m.db"), 18081)
    try:
        r = httpx.get("http://127.0.0.1:18081/health", timeout=5.0)
        assert r.status_code == 200  # no auth on health
        assert r.json() == {"ok": True, "version": 3, "vec": "bruteforce"}
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


def test_http_tool_parity(tmp_path, capsys):
    import asyncio
    from memo.server.cli import main
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    dbp = str(tmp_path / "m.db")
    assert main(["--db", dbp, "--dump-tools"]) == 0
    dumped = sorted(capsys.readouterr().out.split())

    async def _names():
        async with streamablehttp_client(
                "http://127.0.0.1:18083/mcp",
                headers={"Authorization": "Bearer t"}) as (rd, wr, _):
            async with ClientSession(rd, wr) as sess:
                await sess.initialize()
                tools = await sess.list_tools()
                return sorted(t.name for t in tools.tools)
    srv, th = serve(dbp, 18083)
    try:
        assert asyncio.run(_names()) == dumped  # same tools, either transport
    finally:
        srv.should_exit = True
        th.join(timeout=10)
