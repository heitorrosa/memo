"""Task 6 tests: skill_sync dual-mode + L0 join (spec §§20-21).

Routing is by the agent's DECLARED persist verdict, never by name: a non-empty
capabilities declaration yields a file delta; every other caller (undeclared,
bare legacy names, unknown) gets the L0 bundle.
"""

from memo import db, skills
from memo import prompts as prompt_store

# The declaration is an abstract verdict: any non-empty value means "I can
# persist". The server never inspects harness or tool names.
CAN_PERSIST = ["persist"]


def _mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def _put(conn, name, body="body", desc=None):
    return skills.put(conn, name, desc or f"{name} does things.", body)


def _files(conn, **kw):
    """Declared-persistence sync: file mode. The harness name is irrelevant."""
    return skills.sync(conn, "opencode", capabilities=CAN_PERSIST, **kw)


def _known(entries):
    return [{"name": n, "hash": h} for n, h in entries]


def test_empty_converges(tmp_path):
    # T-3/T-6: cold client (known omitted) gets full materialization with
    # frontmatter; echoing hashes back converges to empty deltas (M-021).
    conn = _mem(tmp_path)
    _put(conn, "b-skill", body="# b")
    _put(conn, "a-skill", body="# a")
    first = _files(conn)
    assert first["mode"] == "files" and first["total"] == 2
    assert first["offset"] == 0
    assert [w["path"] for w in first["files_to_write"]] == [
        "skills/a-skill/SKILL.md",
        "skills/b-skill/SKILL.md",
    ]  # name asc, never insertion order (M-014)
    assert first["files_to_write"][0]["content"].startswith(
        "---\nname: a-skill\ndescription:"
    )
    assert first["files_to_write"][0]["content"].endswith("# a")
    assert first["files_to_delete"] == []
    known = _known(
        [(w["path"].split("/")[1], w["hash"]) for w in first["files_to_write"]]
    )
    second = _files(conn, known=known)
    assert second["files_to_write"] == [] and second["files_to_delete"] == []
    assert second["total"] == 2
    third = _files(conn, known=known)  # idempotent repeat
    assert third["files_to_write"] == [] and third["files_to_delete"] == []
    conn.close()


def test_stale_hash_rewrites(tmp_path):
    conn = _mem(tmp_path)
    h = _put(conn, "s", body="v1")["hash"]
    _put(conn, "s", body="v2")
    r = _files(conn, known=_known([("s", h)]))
    assert [w["path"] for w in r["files_to_write"]] == ["skills/s/SKILL.md"]
    assert r["files_to_write"][0]["content"].endswith("v2")
    conn.close()


def test_delete_surfaces_once(tmp_path):
    conn = _mem(tmp_path)
    h = _put(conn, "gone")["hash"]
    skills.tombstone(conn, "gone")
    r = _files(conn, known=_known([("gone", h)]))
    assert [d["path"] for d in r["files_to_delete"]] == ["skills/gone/SKILL.md"]
    assert r["files_to_write"] == []
    assert r["total"] == 0  # tombstones are not live skills
    again = _files(conn, known=[])  # client applied the delete
    assert again["files_to_delete"] == [] and again["files_to_write"] == []
    # Unknown-to-server names the client lists also surface as deletes.
    phantom = _files(conn, known=_known([("phantom", "0" * 64)]))
    assert [d["path"] for d in phantom["files_to_delete"]] == [
        "skills/phantom/SKILL.md"
    ]
    conn.close()


def test_stale_known_resends_delete(tmp_path):
    # F-12: a client that keeps offering the deleted name re-gets the delete
    # until the tombstone expires; the body is never resurrected.
    conn = _mem(tmp_path)
    h = _put(conn, "gone")["hash"]
    skills.tombstone(conn, "gone")
    for _ in range(2):
        r = _files(conn, known=_known([("gone", h)]))
        assert [d["path"] for d in r["files_to_delete"]] == ["skills/gone/SKILL.md"]
        assert r["files_to_write"] == []
    assert skills.get(conn, "gone")["error"] == "not_found"
    conn.close()


def test_pagination_cap(tmp_path):
    conn = _mem(tmp_path)
    for i in range(25):
        _put(conn, f"skill-{i:02d}", body=f"# body {i}\n" + "x" * 100)
    p1 = _files(conn)
    assert len(p1["files_to_write"]) <= 10
    assert sum(len(w["content"]) for w in p1["files_to_write"]) <= 30 * 1024
    assert p1["total"] == 25
    seen, offset = [], 0
    while True:
        p = _files(conn, offset=offset)
        assert len(p["files_to_write"]) <= 10
        if not p["files_to_write"]:
            break
        seen += [w["path"] for w in p["files_to_write"]]
        offset += len(p["files_to_write"])
        assert offset <= 25
    assert len(seen) == 25 and len(set(seen)) == 25  # walked all, no dupes
    assert seen == sorted(seen)  # deterministic name-asc order across pages
    conn.close()


def test_pagination_oversize_single(tmp_path):
    # One skill over 30KB still makes progress: a page holds >= 1 entry.
    conn = _mem(tmp_path)
    _put(conn, "big", body="x" * 40000)
    _put(conn, "small", body="y")
    p1 = _files(conn)
    assert [w["path"] for w in p1["files_to_write"]] == ["skills/big/SKILL.md"]
    p2 = _files(conn, offset=1)
    assert [w["path"] for w in p2["files_to_write"]] == ["skills/small/SKILL.md"]
    assert p2["total"] == 2
    conn.close()


def test_web_bundle_has_l0(tmp_path):
    conn = _mem(tmp_path)
    for n in ("a-deploy", "b-test", "c-grade"):
        _put(conn, n, desc=f"{n} workflow helper.")
    b = skills.sync(conn, "web")
    assert b["mode"] == "bundle"
    assert b["truncated"] is False and b["total"] == 3
    for n in ("a-deploy", "b-test", "c-grade"):
        assert n in b["bootstrap_md"]
    assert "skill_get" in b["bootstrap_md"]
    conn.close()


def test_web_truncation(tmp_path):
    # F-10/M-023: 200 skills -> truncated bundle + filter narrows.
    conn = _mem(tmp_path)
    for i in range(200):
        _put(conn, f"skill-{i:03d}", desc=f"generic helper number {i}.")
    _put(conn, "deploy-ship", desc="Ship the Vite app: build, preview-check, publish.")
    b = skills.sync(conn, "web")
    assert b["truncated"] is True and b["total"] == 201
    assert "skill-000" in b["bootstrap_md"]  # name-asc slice, L0 only
    f = skills.sync(conn, "web", filter="deploy ship vite")
    assert "deploy-ship" in f["bootstrap_md"]
    assert "skill-000" not in f["bootstrap_md"]
    conn.close()


def test_bootstrap_embeds_l0(tmp_path):
    from memo import summary

    conn = _mem(tmp_path)
    _put(conn, "qol-deploy", desc="Ship the Vite app.")
    text = summary.bootstrap_text(conn)
    assert "qol-deploy" in text
    assert "Ship the Vite app." in text
    assert "Stats:" in text and "1 skills" in text
    conn.close()


def test_l0_name_asc(tmp_path):
    conn = _mem(tmp_path)
    for n in ("zebra", "apple", "mango"):
        _put(conn, n)
    assert [l.split("**")[1] for l in skills.l0_index(conn)] == [
        "apple",
        "mango",
        "zebra",
    ]
    conn.close()


def test_undeclared_bundle_by_default(tmp_path):
    # Undeclared call (any name, legacy or unknown) is NOT an error: bundle.
    conn = _mem(tmp_path)
    _put(conn, "qol-deploy", desc="Ship the Vite app.")
    for name in ("carrier-pigeon", "opencode", "web"):
        b = skills.sync(conn, name)
        assert b["mode"] == "bundle" and b["total"] == 1, name
    assert "qol-deploy" in skills.sync(conn, "carrier-pigeon")["bootstrap_md"]
    conn.close()


def test_declared_persist_gets_deltas(tmp_path):
    # The declaration decides, not the harness name: any non-empty declaration
    # (whatever its labels) yields a file delta.
    conn = _mem(tmp_path)
    _put(conn, "qol-deploy", desc="Ship the Vite app.")
    for name in ("opencode", "web", "some-future-harness"):
        r = skills.sync(conn, name, capabilities=["opaque-label"])
        assert r["mode"] == "files" and r["total"] == 1, name
    r = skills.sync(conn, "opencode", capabilities=CAN_PERSIST)
    assert [w["path"] for w in r["files_to_write"]] == [
        "skills/qol-deploy/SKILL.md"
    ]
    conn.close()


def test_empty_declaration_gets_bundle(tmp_path):
    # An empty declaration (None or []) declares no persistence -> bundle.
    conn = _mem(tmp_path)
    _put(conn, "qol-deploy", desc="Ship the Vite app.")
    assert skills.sync(conn, "opencode", capabilities=[])["mode"] == "bundle"
    assert skills.sync(conn, "opencode", capabilities=None)["mode"] == "bundle"
    conn.close()


def test_bare_legacy_names_no_longer_route(tmp_path):
    # Regression pin: bare names that once meant "files" now mean nothing.
    conn = _mem(tmp_path)
    _put(conn, "qol-deploy", desc="Ship the Vite app.")
    for name in ("opencode", "claude-code", "filesystem", "web"):
        assert skills.sync(conn, name)["mode"] == "bundle", name
    conn.close()


def test_harness_names_never_influence_routing(tmp_path):
    # Harness names that once routed (opencode/claude-code/filesystem) or never
    # did (aliens) are treated identically: the declaration governs, not the name.
    conn = _mem(tmp_path)
    _put(conn, "qol-deploy", desc="Ship the Vite app.")
    for alien in ("totally-alien", "claude-code", "filesystem", "opencode", "web"):
        assert skills.sync(conn, alien)["mode"] == "bundle", alien
        assert skills.sync(conn, alien,
                           capabilities=CAN_PERSIST)["mode"] == "files", alien
    conn.close()


def test_capability_labels_are_never_matched(tmp_path):
    # Capability labels are opaque: tool names and junk alike flip the mode only
    # by being present, never by their value. Only emptiness decides.
    conn = _mem(tmp_path)
    _put(conn, "qol-deploy", desc="Ship the Vite app.")
    for label in ("skill_create", "file_write", "SKILL_CREATE", "whatever",
                  "persist"):
        assert skills.sync(conn, "any-name",
                           capabilities=[label])["mode"] == "files", label
    assert skills.sync(conn, "any-name", capabilities=[])["mode"] == "bundle"
    conn.close()


def test_malformed_harness_not_found(tmp_path):
    conn = _mem(tmp_path)
    assert skills.sync(conn, "")["error"] == "not_found"
    assert skills.sync(conn, None)["error"] == "not_found"  # type: ignore[arg-type]
    conn.close()


def test_sync_prompt_teaches_self_evaluation():
    # The decision surface is the agent judging its OWN persistence, then
    # mapping that verdict to a declaration -- not a name lookup.
    text = prompt_store.render("tool_skill_sync")
    for needle in (
        "SELF-EVALUATION",
        "Can I write files that persist across sessions?",
        "skill-create or file-write tool",
        "materialized a skill file before",
        "sandbox-only with no persistence",
        "declare that you can persist and receive a file delta",
        "declare that you have no persistence and receive the L0 index bundle",
        "NEVER matches harness or tool names",
        "apply deletes THEN writes",
        "the database row remains truth",
        "re-calls sync with a filter",
    ):
        assert needle in text, f"missing self-evaluation instruction: {needle!r}"


def test_sync_prompt_names_no_longer_route():
    # The migration note states names no longer route; the receipt shapes
    # (mode/fields/offset) are unchanged.
    text = prompt_store.render("tool_skill_sync")
    assert "opencode, claude-code, filesystem" in text  # migration note
    assert "no longer route" in text
    assert "declaration" in text and "skill_create, file_write" not in text
    assert "files_to_write (path, content, hash)" in text
    assert "files_to_delete (path)" in text
    assert "offset, total" in text
    assert "bootstrap_md, truncated, total" in text
    assert "not_found" in text
