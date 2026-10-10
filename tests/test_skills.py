"""Task 5 tests: skills put/get/update/install + tombstone (spec §§18-19)."""

import hashlib

from memo import db
from memo.skills.crud import get, l0_index, put, tombstone, update
from memo.skills.install import install


def _mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_put_get_roundtrip(tmp_path):
    conn = _mem(tmp_path)
    r = put(conn, "qol-deploy", "Ship the Vite app.", "# QoL deploy\nsteps")
    assert r == {"hash": _sha("# QoL deploy\nsteps"), "version": 1}
    g = get(conn, "qol-deploy")
    assert (g["name"], g["description"], g["body"], g["version"]) == (
        "qol-deploy",
        "Ship the Vite app.",
        "# QoL deploy\nsteps",
        1,
    )
    assert g["hash"] == r["hash"]
    conn.close()


def test_put_idempotent(tmp_path):
    conn = _mem(tmp_path)
    a = put(conn, "s", "d", "same body")
    b = put(conn, "s", "d", "same body")
    assert b == a  # identical body: no version bump (misunderstanding #8)
    assert get(conn, "s")["version"] == 1
    conn.close()


def test_put_bumps(tmp_path):
    conn = _mem(tmp_path)
    put(conn, "s", "d", "v1")
    r = put(conn, "s", "d", "v2")
    assert r["version"] == 2 and r["hash"] == _sha("v2")
    assert get(conn, "s")["body"] == "v2"
    conn.close()


def test_update_appends_revision(tmp_path):
    conn = _mem(tmp_path)
    put(conn, "s", "d", "base body")
    r = update(conn, "s", "prefer pnpm over npm")
    assert r["version"] == 2 and r["hash"] == _sha(get(conn, "s")["body"])
    body = get(conn, "s")["body"]
    assert body.startswith("base body")  # history never rewritten
    assert "## Revision v2" in body and "prefer pnpm over npm" in body
    old = get(conn, "s", version=1)
    assert old["body"] == "base body" and old["version"] == 1
    assert get(conn, "s", version=99)["error"] == "not_found"
    assert update(conn, "ghost", "x")["error"] == "not_found"
    conn.close()


def test_get_miss(tmp_path):
    conn = _mem(tmp_path)
    assert get(conn, "nope")["error"] == "not_found"
    conn.close()


def test_concurrent_loser_preserved(tmp_path):
    # M-019: two writers on the same base version -> last wins, loser kept
    # as an appended `## Revision vN (superseded, ...)` section, never lost.
    conn = _mem(tmp_path)
    put(conn, "s", "d", "v1")
    a = put(conn, "s", "d", "A-body", base_version=1)
    assert a["version"] == 2
    b = put(conn, "s", "d", "B-body", base_version=1)  # stale base
    assert b["version"] == 3
    body = get(conn, "s")["body"]
    assert body.startswith("B-body")
    assert "superseded" in body and "A-body" in body
    conn.close()


def test_tombstone(tmp_path):
    conn = _mem(tmp_path)
    put(conn, "s", "d", "body")
    t = tombstone(conn, "s")
    assert t["source"] == "tombstone"
    assert get(conn, "s")["error"] == "not_found"  # deleted: invisible
    assert l0_index(conn) == []
    assert update(conn, "s", "x")["error"] == "not_found"
    assert tombstone(conn, "ghost")["error"] == "not_found"
    r = put(conn, "s", "d", "body v2")  # Q-04: resurrect, counter continues
    assert r["version"] == 2
    assert get(conn, "s")["body"] == "body v2"
    conn.close()


SKILL_MD = (
    "---\n"
    "name: pytest-fast\n"
    "description: Run the repo test subset fast.\n"
    "version: 1\n"
    "---\n"
    "# pytest-fast\nrun pytest -x -q\n"
)


def test_install_validates_frontmatter(tmp_path):
    conn = _mem(tmp_path)
    seen = {}

    def fetch(url):
        seen["url"] = url
        return SKILL_MD

    r = install(conn, "github:acme/dotfiles", "pytest-fast", fetch=fetch)
    assert r["version"] == 1 and r["hash"] == _sha(
        "# pytest-fast\nrun pytest -x -q"
    )
    assert "pytest-fast" in seen["url"]
    assert "raw.githubusercontent.com" in seen["url"]
    g = get(conn, "pytest-fast")
    assert g["description"] == "Run the repo test subset fast."
    assert len(r["files_to_write"]) == 1
    assert r["files_to_write"][0]["path"] == "skills/pytest-fast/SKILL.md"

    sh = install(conn, "skills.sh/acme", "pytest-fast", fetch=fetch)
    assert sh["version"] == 1  # identical body: idempotent, no bump

    m = install(conn, "github:acme/dotfiles", "other-name", fetch=fetch)
    assert m["error"] == "not_found" and "mismatch" in m["detail"]

    bad = install(
        conn, "github:acme/dotfiles", "x", fetch=lambda url: "no frontmatter here"
    )
    assert bad["error"] == "not_found"
    assert get(conn, "x").get("error") == "not_found"  # no row written

    nodesc = install(
        conn,
        "github:acme/dotfiles",
        "x",
        fetch=lambda url: "---\nname: x\n---\nbody\n",
    )
    assert nodesc["error"] == "not_found"
    assert get(conn, "x").get("error") == "not_found"
    conn.close()


def test_install_fetch_404(tmp_path):
    # F-04: fetch failure -> isError, no row, no delta.
    conn = _mem(tmp_path)

    def fetch(url):
        raise RuntimeError("fetch failed: HTTP 404 for " + url)

    r = install(conn, "github:acme/dotfiles", "ghost", fetch=fetch)
    assert r["error"] == "internal" and "fetch failed" in r["detail"]
    assert r.get("files_to_write") is None
    assert get(conn, "ghost").get("error") == "not_found"
    assert install(conn, "weird://x", "ghost")["error"] == "not_found"
    conn.close()
