"""Task 6 tests: skill_sync dual-mode + L0 join (spec §§20-21)."""

from memo import db, skills


def _mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def _put(conn, name, body="body", desc=None):
    return skills.put(conn, name, desc or f"{name} does things.", body)


def _known(entries):
    return [{"name": n, "hash": h} for n, h in entries]


def test_empty_converges(tmp_path):
    # T-3/T-6: cold client (known omitted) gets full materialization with
    # frontmatter; echoing hashes back converges to empty deltas (M-021).
    conn = _mem(tmp_path)
    _put(conn, "b-skill", body="# b")
    _put(conn, "a-skill", body="# a")
    first = skills.sync(conn, "opencode")
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
    second = skills.sync(conn, "opencode", known=known)
    assert second["files_to_write"] == [] and second["files_to_delete"] == []
    assert second["total"] == 2
    third = skills.sync(conn, "opencode", known=known)  # idempotent repeat
    assert third["files_to_write"] == [] and third["files_to_delete"] == []
    conn.close()


def test_stale_hash_rewrites(tmp_path):
    conn = _mem(tmp_path)
    h = _put(conn, "s", body="v1")["hash"]
    _put(conn, "s", body="v2")
    r = skills.sync(conn, "opencode", known=_known([("s", h)]))
    assert [w["path"] for w in r["files_to_write"]] == ["skills/s/SKILL.md"]
    assert r["files_to_write"][0]["content"].endswith("v2")
    conn.close()


def test_delete_surfaces_once(tmp_path):
    conn = _mem(tmp_path)
    h = _put(conn, "gone")["hash"]
    skills.tombstone(conn, "gone")
    r = skills.sync(conn, "opencode", known=_known([("gone", h)]))
    assert [d["path"] for d in r["files_to_delete"]] == ["skills/gone/SKILL.md"]
    assert r["files_to_write"] == []
    assert r["total"] == 0  # tombstones are not live skills
    again = skills.sync(conn, "opencode", known=[])  # client applied the delete
    assert again["files_to_delete"] == [] and again["files_to_write"] == []
    # Unknown-to-server names the client lists also surface as deletes.
    phantom = skills.sync(conn, "opencode", known=_known([("phantom", "0" * 64)]))
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
        r = skills.sync(conn, "opencode", known=_known([("gone", h)]))
        assert [d["path"] for d in r["files_to_delete"]] == ["skills/gone/SKILL.md"]
        assert r["files_to_write"] == []
    assert skills.get(conn, "gone")["error"] == "not_found"
    conn.close()


def test_pagination_cap(tmp_path):
    conn = _mem(tmp_path)
    for i in range(25):
        _put(conn, f"skill-{i:02d}", body=f"# body {i}\n" + "x" * 100)
    p1 = skills.sync(conn, "opencode")
    assert len(p1["files_to_write"]) <= 10
    assert sum(len(w["content"]) for w in p1["files_to_write"]) <= 30 * 1024
    assert p1["total"] == 25
    seen, offset = [], 0
    while True:
        p = skills.sync(conn, "opencode", offset=offset)
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
    p1 = skills.sync(conn, "opencode")
    assert [w["path"] for w in p1["files_to_write"]] == ["skills/big/SKILL.md"]
    p2 = skills.sync(conn, "opencode", offset=1)
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


def test_unknown_harness(tmp_path):
    conn = _mem(tmp_path)
    assert skills.sync(conn, "carrier-pigeon")["error"] == "not_found"
    conn.close()
