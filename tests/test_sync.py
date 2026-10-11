from memo import db
from memo.skills.crud import get, l0_index, put, tombstone
from memo.skills.sync import sync
from memo import prompts as prompt_store

CAN_PERSIST = ["persist"]


def mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def put_skill(conn, name, body="body", desc=None):
    return put(conn, name, desc or f"{name} does things.", body)


def files_delta(conn, **kw):
    return sync(conn, "opencode", capabilities=CAN_PERSIST, **kw)


def known_list(entries):
    return [{"name": n, "hash": h} for n, h in entries]


def test_empty_converges(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "b-skill", body="# b")
    put_skill(conn, "a-skill", body="# a")
    first = files_delta(conn)
    assert first["mode"] == "files" and first["total"] == 2
    assert first["offset"] == 0
    assert [w["path"] for w in first["files_to_write"]] == [
        "skills/a-skill/SKILL.md",
        "skills/b-skill/SKILL.md",
    ]
    assert first["files_to_write"][0]["content"].startswith(
        "---\nname: a-skill\ndescription:"
    )
    assert first["files_to_write"][0]["content"].endswith("# a")
    assert first["files_to_delete"] == []
    known = known_list(
        [(w["path"].split("/")[1], w["hash"]) for w in first["files_to_write"]]
    )
    second = files_delta(conn, known=known)
    assert second["files_to_write"] == [] and second["files_to_delete"] == []
    assert second["total"] == 2
    third = files_delta(conn, known=known)
    assert third["files_to_write"] == [] and third["files_to_delete"] == []
    conn.close()


def test_stale_hash_rewrites(tmp_path):
    conn = mem(tmp_path)
    h = put_skill(conn, "s", body="v1")["hash"]
    put_skill(conn, "s", body="v2")
    r = files_delta(conn, known=known_list([("s", h)]))
    assert [w["path"] for w in r["files_to_write"]] == ["skills/s/SKILL.md"]
    assert r["files_to_write"][0]["content"].endswith("v2")
    conn.close()


def test_delete_surfaces_once(tmp_path):
    conn = mem(tmp_path)
    h = put_skill(conn, "gone")["hash"]
    tombstone(conn, "gone")
    r = files_delta(conn, known=known_list([("gone", h)]))
    assert [d["path"] for d in r["files_to_delete"]] == ["skills/gone/SKILL.md"]
    assert r["files_to_write"] == []
    assert r["total"] == 0
    again = files_delta(conn, known=[])
    assert again["files_to_delete"] == [] and again["files_to_write"] == []
    phantom = files_delta(conn, known=known_list([("phantom", "0" * 64)]))
    assert [d["path"] for d in phantom["files_to_delete"]] == [
        "skills/phantom/SKILL.md"
    ]
    conn.close()


def test_stale_known_resends_delete(tmp_path):
    conn = mem(tmp_path)
    h = put_skill(conn, "gone")["hash"]
    tombstone(conn, "gone")
    for _ in range(2):
        r = files_delta(conn, known=known_list([("gone", h)]))
        assert [d["path"] for d in r["files_to_delete"]] == ["skills/gone/SKILL.md"]
        assert r["files_to_write"] == []
    assert get(conn, "gone")["error"] == "not_found"
    conn.close()


def test_pagination_cap(tmp_path):
    conn = mem(tmp_path)
    for i in range(25):
        put_skill(conn, f"skill-{i:02d}", body=f"# body {i}\n" + "x" * 100)
    p1 = files_delta(conn)
    assert len(p1["files_to_write"]) <= 10
    assert sum(len(w["content"]) for w in p1["files_to_write"]) <= 30 * 1024
    assert p1["total"] == 25
    seen, offset = [], 0
    while True:
        p = files_delta(conn, offset=offset)
        assert len(p["files_to_write"]) <= 10
        if not p["files_to_write"]:
            break
        seen += [w["path"] for w in p["files_to_write"]]
        offset += len(p["files_to_write"])
        assert offset <= 25
    assert len(seen) == 25 and len(set(seen)) == 25
    assert seen == sorted(seen)
    conn.close()


def test_pagination_oversize_single(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "big", body="x" * 40000)
    put_skill(conn, "small", body="y")
    p1 = files_delta(conn)
    assert [w["path"] for w in p1["files_to_write"]] == ["skills/big/SKILL.md"]
    p2 = files_delta(conn, offset=1)
    assert [w["path"] for w in p2["files_to_write"]] == ["skills/small/SKILL.md"]
    assert p2["total"] == 2
    conn.close()


def test_web_bundle_has_l0(tmp_path):
    conn = mem(tmp_path)
    for n in ("a-deploy", "b-test", "c-grade"):
        put_skill(conn, n, desc=f"{n} workflow helper.")
    b = sync(conn, "web")
    assert b["mode"] == "bundle"
    assert b["truncated"] is False and b["total"] == 3
    for n in ("a-deploy", "b-test", "c-grade"):
        assert n in b["bootstrap_md"]
    assert "skill_get" in b["bootstrap_md"]
    conn.close()


def test_web_truncation(tmp_path):
    conn = mem(tmp_path)
    for i in range(200):
        put_skill(conn, f"skill-{i:03d}", desc=f"generic helper number {i}.")
    put_skill(conn, "deploy-ship", desc="Ship the Vite app: build, preview-check, publish.")
    b = sync(conn, "web")
    assert b["truncated"] is True and b["total"] == 201
    assert "skill-000" in b["bootstrap_md"]
    f = sync(conn, "web", filter="deploy ship vite")
    assert "deploy-ship" in f["bootstrap_md"]
    assert "skill-000" not in f["bootstrap_md"]
    conn.close()


def test_bootstrap_embeds_l0(tmp_path):
    from memo import summary

    conn = mem(tmp_path)
    put_skill(conn, "qol-deploy", desc="Ship the Vite app.")
    text = summary.bootstrap_text(conn)
    assert "qol-deploy" in text
    assert "Ship the Vite app." in text
    assert "Stats:" in text and "1 skills" in text
    conn.close()


def test_l0_name_asc(tmp_path):
    conn = mem(tmp_path)
    for n in ("zebra", "apple", "mango"):
        put_skill(conn, n)
    assert [l.split("**")[1] for l in l0_index(conn)] == [
        "apple",
        "mango",
        "zebra",
    ]
    conn.close()


def test_undeclared_bundle_by_default(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "qol-deploy", desc="Ship the Vite app.")
    for name in ("carrier-pigeon", "opencode", "web"):
        b = sync(conn, name)
        assert b["mode"] == "bundle" and b["total"] == 1, name
    assert "qol-deploy" in sync(conn, "carrier-pigeon")["bootstrap_md"]
    conn.close()


def test_declared_persist_gets_deltas(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "qol-deploy", desc="Ship the Vite app.")
    for name in ("opencode", "web", "some-future-harness"):
        r = sync(conn, name, capabilities=["opaque-label"])
        assert r["mode"] == "files" and r["total"] == 1, name
    r = sync(conn, "opencode", capabilities=CAN_PERSIST)
    assert [w["path"] for w in r["files_to_write"]] == [
        "skills/qol-deploy/SKILL.md"
    ]
    conn.close()


def test_empty_declaration_gets_bundle(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "qol-deploy", desc="Ship the Vite app.")
    assert sync(conn, "opencode", capabilities=[])["mode"] == "bundle"
    assert sync(conn, "opencode", capabilities=None)["mode"] == "bundle"
    conn.close()


def test_bare_legacy_names_no_longer_route(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "qol-deploy", desc="Ship the Vite app.")
    for name in ("opencode", "claude-code", "filesystem", "web"):
        assert sync(conn, name)["mode"] == "bundle", name
    conn.close()


def test_harness_names_never_influence_routing(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "qol-deploy", desc="Ship the Vite app.")
    for alien in ("totally-alien", "claude-code", "filesystem", "opencode", "web"):
        assert sync(conn, alien)["mode"] == "bundle", alien
        assert sync(conn, alien,
                           capabilities=CAN_PERSIST)["mode"] == "files", alien
    conn.close()


def test_capability_labels_are_never_matched(tmp_path):
    conn = mem(tmp_path)
    put_skill(conn, "qol-deploy", desc="Ship the Vite app.")
    for label in ("skill_create", "file_write", "SKILL_CREATE", "whatever",
                  "persist"):
        assert sync(conn, "any-name",
                           capabilities=[label])["mode"] == "files", label
    assert sync(conn, "any-name", capabilities=[])["mode"] == "bundle"
    conn.close()


def test_malformed_harness_not_found(tmp_path):
    conn = mem(tmp_path)
    assert sync(conn, "")["error"] == "not_found"
    assert sync(conn, None)["error"] == "not_found"
    conn.close()


def test_sync_prompt_teaches_self_evaluation():
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
    import pathlib

    import memo.skills.sync as sync_mod
    text = prompt_store.render("tool_skill_sync")
    assert "opencode, claude-code, filesystem" not in text
    assert "no longer route" in text
    assert "declaration" in text and "skill_create, file_write" not in text
    assert "capabilities" in text
    source = pathlib.Path(sync_mod.__file__).read_text(encoding="utf-8")
    assert "opencode, claude-code, filesystem, web" in source
    assert "files_to_write (path, content, hash)" in text
    assert "files_to_delete (path)" in text
    assert "offset, total" in text
    assert "bootstrap_md, truncated, total" in text
    assert "not_found" in text


def test_sync_prompt_documents_hash_recipe():
    text = prompt_store.render("tool_skill_sync")
    assert "sha256-hex" in text
    assert "frontmatter block removed" in text
    assert "whitespace trimmed" in text
    assert "hash_body_normalized" in text


def test_trailing_whitespace_no_churn(tmp_path):
    from memo.skills.crud import hash_body_normalized
    conn = mem(tmp_path)
    put_skill(conn, "s", body="body text\n")
    put_skill(conn, "t", body="clean body")
    known = known_list([
        ("s", hash_body_normalized("body text")),
        ("t", hash_body_normalized("clean body\n")),
    ])
    r = files_delta(conn, known=known)
    assert r["files_to_write"] == [] and r["files_to_delete"] == []
    conn.close()


def test_emitted_hash_is_normalized(tmp_path):
    from memo.skills.crud import hash_body_normalized
    conn = mem(tmp_path)
    put_skill(conn, "s", body="body text\n")
    w = files_delta(conn)["files_to_write"][0]
    assert w["hash"] == hash_body_normalized("body text\n")
    conn.close()


def test_web_bundle_has_memories(tmp_path):
    from memo import embeddings
    from memo.memory.save import save
    embeddings.force_fallback = True
    try:
        conn = mem(tmp_path)
        put_skill(conn, "qol-deploy", desc="Ship the Vite app.")
        empty = sync(conn, "web")
        assert empty["mode"] == "bundle"
        assert "Top memories (0)" in empty["bootstrap_md"]
        assert "(none yet)" in empty["bootstrap_md"]
        save(conn, "context.home", "lives in Campinas for the atlas project")
        full = sync(conn, "web")
        assert "Top memories (1)" in full["bootstrap_md"]
        assert "context.home" in full["bootstrap_md"]
        assert "qol-deploy" in full["bootstrap_md"]
        conn.close()
    finally:
        embeddings.force_fallback = False
