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
    now = datetime.datetime.now(datetime.timezone.utc)
    best, best_e = MISSING, None
    for key, uses, stab, ca in conn.execute(
            "SELECT key, uses, stability, created_at FROM memories"
            " WHERE namespace=? AND status='current'", (ns,)):
        age = max(0.0, (now - datetime.datetime.fromisoformat(ca))
                  .total_seconds() / 86400)
        if age < 1.0:
            continue
        e = (uses / max(age, 1.0)) * math.exp(-age / stab)
        if best_e is None or e < best_e:
            best, best_e = key, e
    assert best is not MISSING
    return best


CAP = 2000


def fill_2000(conn):
    for i in range(CAP - 1):
        insert(conn, f"fillrow{i:04d}", age_days=2.0 + (i % 25),
               uses=1 + (i % 6))
    insert(conn, "old.deploy.cmd", typ="preference", age_days=120.0,
           uses=1, score=1.0,
           value="durable project fact: the legacy deploy command lives in ops docs")
    assert count(conn) == CAP


def test_2001st_evicts_one(tmp_path):
    from memo.memory.save import save
    conn = mem(tmp_path)
    fill_2000(conn)
    expected = expected_victim(conn)
    assert expected == "old.deploy.cmd"
    r = save(conn, "zebrafinch", "durable project fact: the team writes Python services with uv")
    assert r["action"] == "created"
    assert r["archived"] == expected
    assert count(conn) == CAP
    assert conn.execute(
        "SELECT archived_reason FROM archived_memories WHERE key=?",
        (expected,)).fetchone()[0] == "cap_eviction"
    assert conn.execute(
        "SELECT 1 FROM memories WHERE key='zebrafinch'").fetchone()
    conn.close()


def test_burst_grace(tmp_path):
    from memo.memory.save import save
    conn = mem(tmp_path)
    fill_2000(conn)
    fresh = ["zebrafinch", "quixotic", "wamblegronk", "xyloid", "kryften",
             "vexillary", "plumbus", "zorgblatt", "quandle", "fimble"]
    for k in fresh:
        r = save(conn, k, f"durable project fact about {k} for the deploy pipeline")
        assert r["action"] == "created", k
    for k in fresh:
        assert conn.execute(
            "SELECT 1 FROM memories WHERE key=?", (k,)).fetchone(), k
    victims = [x[0] for x in conn.execute(
        "SELECT key FROM archived_memories WHERE archived_reason='cap_eviction'")]
    assert len(victims) == 10
    now = datetime.datetime.now(datetime.timezone.utc)
    for v in victims:
        ca = conn.execute(
            "SELECT created_at FROM archived_memories WHERE key=?", (v,)).fetchone()[0]
        age = (now - datetime.datetime.fromisoformat(ca)).total_seconds() / 86400
        assert age >= 1.0, v
    conn.close()


def test_superseded_excluded_from_cap(tmp_path):
    from memo.memory.save import save
    conn = mem(tmp_path)
    fill_2000(conn)
    for i in range(5):
        insert(conn, f"hist.old.{i}", status="superseded", age_days=50.0, uses=1)
    r = save(conn, "zebrafinch", "durable project fact: the team writes Python services with uv")
    assert r["action"] == "created"
    assert count(conn) == CAP
    assert count(conn, status="superseded") == 5
    conn.close()


def test_eviction_bounded(tmp_path):
    from memo import maintenance
    conn = mem(tmp_path)
    fill_2000(conn)
    for i in range(3):
        insert(conn, f"over{i}", age_days=20.0, uses=1)
    out = maintenance.run(conn, max_moves=2)
    assert len(out["archived"]) == 2
    assert count(conn) == CAP + 1
    out2 = maintenance.run(conn)
    assert count(conn) == CAP and out2["evicted"] is not None
    conn.close()


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
    {"score": 0.5},
    {"uses": 3},
    {"age_days": 10.0},
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
    assert count(conn) == 2
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
    assert count(conn) == 0
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
    r = save(conn, "fresh.note", "durable project fact: just learned this minute")
    assert r["action"] == "created"
    assert conn.execute(
        "SELECT 1 FROM memories WHERE key='fresh.note'").fetchone()
    conn.close()
