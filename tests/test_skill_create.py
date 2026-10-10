"""skill_create: format-parity with skill_put, create-conflict receipt, chain.

The user-ordered contract: skill_create is FORMAT-IDENTICAL to skill_put (same
params, same receipt) and is insert-only (an existing name is a conflict in the
hash_mismatch family); the create -> write SKILL.md -> skill_put chain lives in
the prompt text, never as hidden server chaining.
"""
import asyncio
import hashlib

from memo import db
from memo.server.descriptions import DESCRIPTIONS
from memo.skills.crud import create, get, put


def _mem(tmp_path, name="memo.db"):
    conn = db.connect(str(tmp_path / name))
    assert db.migrate(conn) == 3
    return conn


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_create_new_matches_put_for_new(tmp_path):
    # Receipt-shape parity: a new name via create == the same name via put.
    a = _mem(tmp_path, "a.db")
    b = _mem(tmp_path, "b.db")
    ra = create(a, "qol-deploy", "Ship it.", "# body")
    rb = put(b, "qol-deploy", "Ship it.", "# body")
    assert ra == rb == {"hash": _sha("# body"), "version": 1}
    assert get(a, "qol-deploy")["body"] == "# body"
    a.close()
    b.close()


def test_create_existing_is_conflict(tmp_path):
    conn = _mem(tmp_path)
    put(conn, "s", "d", "v1")
    r = create(conn, "s", "d", "v2")
    assert r["error"] == "hash_mismatch" and "s" in r["detail"]
    g = get(conn, "s")
    assert g["body"] == "v1" and g["version"] == 1  # existing row untouched
    conn.close()


def test_tool_schema_parity(tmp_path):
    # The two MCP tool definitions are identical in params + required; the only
    # difference is FastMCP's auto-generated top-level title (derived from the
    # function name), which is framework noise, not a format difference.
    from memo.server.core import build_server
    server = build_server(str(tmp_path / "m.db"))
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    a = {k: v for k, v in tools["skill_create"].inputSchema.items() if k != "title"}
    b = {k: v for k, v in tools["skill_put"].inputSchema.items() if k != "title"}
    assert a == b
    assert set(a["properties"]) == {"name", "description", "body", "source"}
    assert a["required"] == ["name", "description", "body"]


def test_prompt_documents_chain():
    text = DESCRIPTIONS["skill_create"]
    assert "skill_put" in text and "SKILL.md" in text
