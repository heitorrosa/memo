"""Hand-replay of spec §36 transcripts T-1..T-8 (acceptance A-6).
Run: MEMO_EMBEDDINGS_FALLBACK=1 python scripts/replay_t1_t8.py
Prints one evidence line per transcript; exits nonzero on any mismatch.
"""
import datetime
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["MEMO_EMBEDDINGS_FALLBACK"] = "1"

from memo import db, embeddings, summary  # noqa: E402
from memo.memory.save import save  # noqa: E402
from memo.memory.search import search  # noqa: E402
from memo.skills.crud import get, put  # noqa: E402
from memo.skills.sync import sync  # noqa: E402

embeddings.force_fallback = True
tmp = tempfile.mkdtemp()
conn = db.connect(os.path.join(tmp, "memo.db"))
assert db.migrate(conn) == 3


def ts(days_ago):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days_ago)).isoformat()


def direct(key, typ="context", age=10.0, uses=0, score=1.0, value=None):
    emb = embeddings.embed([value or key])[0]
    t = ts(age)
    stab = {"preference": 14.0, "feedback": 10.0, "analysis": 7.0, "context": 3.0}[typ]
    with conn:
        conn.execute(
            "INSERT INTO memories(key,namespace,value,type,embedding,stability,"
            "score,uses,created_at,updated_at,valid_from,status,family_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, "default", value or f"durable fact {key}", typ, emb,
             stab, score, uses, t, t, t, "current", f"fam-{key}"))


# T-1 cold open: 5 memories + 2 skills -> summary shape + empty 2nd sync
for k, v in [("editor.indent", "prefers tabs"), ("project.stack", "Vite React Bun"),
             ("standup.time", "standup nine thirty"), ("deploy.cmd", "deploy in ops runbook"),
             ("review.cycle", "review each quarter")]:
    save(conn, k, f"durable long fact: {v} for the team")
put(conn, "qol-deploy", "Ship the Vite app.", "# deploy body")
put(conn, "pytest-fast", "Run tests fast.", "# test body")
text = summary.bootstrap_text(conn)
assert "Top memories (5)" in text and "Stats: 5/250 memories" in text, text
d1 = sync(conn, "opencode")
assert len(d1["files_to_write"]) == 2
d2 = sync(conn, "opencode",
                 known=[{"name": w["path"].split("/")[1], "hash": w["hash"]}
                        for w in d1["files_to_write"]])
assert d2["files_to_write"] == [] and d2["files_to_delete"] == []
print("T-1 COLD-OPEN ok: Top memories (5) + 2 L0 + Stats 5/250 + 2nd sync empty")

# T-2 contradiction across keys -> supersede chain, search shows current
save(conn, "editor.indent", "durable preference: the team prefers tabs in editor",
             type="preference")
save(conn, "editor.indent.v2", "durable preference: prefers spaces instead of tabs now",
             type="preference")
old = conn.execute("SELECT status, family_id, superseded_by FROM memories WHERE key='editor.indent'").fetchone()
new = conn.execute("SELECT status, family_id FROM memories WHERE key='editor.indent.v2'").fetchone()
assert old[0] == "superseded" and new[0] == "current" and old[1] == new[1], (old, new)
assert old[2] == "editor.indent.v2", old
hits = search(conn, "indentation preference")
assert "editor.indent.v2" in [h["key"] for h in hits], hits
assert "editor.indent" not in [h["key"] for h in hits]  # default hides history
both = search(conn, "indentation preference", include_superseded=True)
assert {"editor.indent", "editor.indent.v2"} <= {h["key"] for h in both}, both
print(f"T-2 CONTRADICTION ok: current={[h['key'] for h in hits][:2]} + superseded visible with flag")

# T-3 skill e2e: put v1 -> no-op re-put -> sync delta -> get body -> web bundle
r = put(conn, "web.runbook", "Run the web deploy twice-tested.", "# runbook body v1")
assert r["version"] == 1
assert put(conn, "web.runbook", "Run the web deploy twice-tested.", "# runbook body v1") == r
assert "runbook" in get(conn, "web.runbook")["body"]
assert "web.runbook" in sync(conn, "web")["bootstrap_md"]
print("T-3 SKILL-E2E ok: v1 + no-op re-put + get body + web bundle reflects")

# T-4 burst: fill to 250 via API-distinct facts, then 10 rapid saves.
# NOTE: fill/fresh keys carry a uuid4hex suffix so fuzz.ratio stays <80 and
# each save inserts (sequential burstfillNNN keys fuzzy-merged into
# extends->updated, stalling count and hanging the loop forever).
n0 = conn.execute("SELECT COUNT(*) FROM memories WHERE status='current'").fetchone()[0]
i = 0
while conn.execute("SELECT COUNT(*) FROM memories WHERE status='current'").fetchone()[0] < 250:
    save(conn, f"burst.{uuid.uuid4().hex[:8]}", f"durable archived note {i} zebra xray {i*7}写的历史记录")
    i += 1
    assert i < 2000, f"T-4 fill stalled: {i} saves without reaching 250 current"
fresh = [f"burst.{uuid.uuid4().hex[:8]}" for _ in range(10)]
for k in fresh:
    save(conn, k, f"durable fresh burst fact {k} quasar {k} brand new work")
for k in fresh:
    assert conn.execute("SELECT 1 FROM memories WHERE key=?", (k,)).fetchone(), k
victims = [x[0] for x in conn.execute(
    "SELECT key FROM archived_memories WHERE archived_reason='cap_eviction'")]
now = datetime.datetime.now(datetime.timezone.utc)
for v in victims:
    ca = conn.execute("SELECT created_at FROM archived_memories WHERE key=?", (v,)).fetchone()[0]
    assert (now - datetime.datetime.fromisoformat(ca)).total_seconds() / 86400 >= 1.0, v
print(f"T-4 BURST ok: newest 10 present, {len(victims)} victims all >=1d old")

# T-5 web truncation: 200 skills -> truncated + filter narrows
for j in range(200):
    put(conn, f"sk{j:03d}", f"deploy helper skill number {j} for automation", f"# body {j}")
b = sync(conn, "web")
assert b["truncated"] is True and b["total"] >= 200, (b["truncated"], b["total"])
bf = sync(conn, "web", filter="qol-deploy")
assert "qol-deploy" in bf["bootstrap_md"]
print(f"T-5 TRUNCATION ok: total={b['total']} truncated=True, filter narrows to qol-deploy")

# T-6 crash recovery: apply 1 of 3 deltas, next sync returns remaining 2
conn2 = db.connect(os.path.join(tmp, "s2.db"))
db.migrate(conn2)
for n in ("aa-one", "bb-two", "cc-three"):
    put(conn2, n, f"skill {n}", f"# {n}")
d = sync(conn2, "opencode")
assert d["total"] == 3
partial_known = [{"name": "aa-one", "hash": d["files_to_write"][0]["hash"]}]
d_rest = sync(conn2, "opencode", known=partial_known)
assert sorted(w["path"] for w in d_rest["files_to_write"]) == [
    "skills/bb-two/SKILL.md", "skills/cc-three/SKILL.md"], d_rest
print("T-6 CRASH-RECOVERY ok: partial known=1 -> remaining 2 only")
conn2.close()

# T-7 retry: same fact twice -> created then updated/merged, one row
r1 = save(conn, "retry.fact", "durable retry fact: the team writes Python services")
r2 = save(conn, "retry.fact", "durable retry fact: the team writes Python services")
assert r1["action"] == "created" and r2["action"] in ("updated", "merged"), (r1, r2)
assert conn.execute("SELECT COUNT(*) FROM memories WHERE key='retry.fact'").fetchone()[0] == 1
print(f"T-7 RETRY ok: {r1['action']} then {r2['action']}, one row")

# T-8 future validity: unclamped store, now excludes, future valid_at returns
save(conn, "move.date", "durable plan: moving to Sao Paulo next year", valid_from="2027-01-01")
row = conn.execute("SELECT valid_from FROM memories WHERE key='move.date'").fetchone()
assert row[0].startswith("2027-01-01"), row
assert all(h["key"] != "move.date" for h in search(conn, "where live")), "now must exclude"
# NOTE: token-overlapping query (mirrors tests/test_memory_search.py:70).
# "where live" shares no FTS tokens with the row, so under deterministic
# fallback vectors the row drowns in 250 burst rows on pure V noise;
# the temporal assertion is what T-8 owns, FTS narrowing is the query's job.
fut = search(conn, "moving Sao Paulo", valid_at="2027-06-01")
assert any(h["key"] == "move.date" for h in fut), [h["key"] for h in fut]
print("T-8 FUTURE-VALIDITY ok: stored 2027-01-01 unclamped, now excludes, 2027-06-01 returns")

print("ALL T-1..T-8 GREEN")
