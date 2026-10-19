import sqlite3
import subprocess
import sys
from pathlib import Path

from memo.db import connect, migrate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backfill_graph.py"


def vec(text: str) -> bytes:
    import hashlib

    import numpy as np

    seed_value = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "little")
    generator = np.random.RandomState(seed_value)
    vector = generator.rand(384).astype(np.float32) - 0.5
    vector /= np.linalg.norm(vector) + 1e-12
    return vector.tobytes()


def seed(path: Path) -> None:
    conn = connect(str(path))
    migrate(conn)
    rows = [
        ("preference.frontend-stack-core", "User prefers Tailwind for styling.", "preference", "shared"),
        ("preference.frontend-stack-motion", "User prefers motion for animation.", "preference", "shared"),
        ("Design preferences for Mansa UI", "Apple Liquid Glass, light theme.", "preference", "alone"),
    ]
    family = "fam-frontend"
    for index, (key, value, kind, vector_of) in enumerate(rows):
        conn.execute(
            "INSERT INTO memories(key, namespace, value, type, embedding, status, stability, score, uses,"
            " family_id, created_at, updated_at, valid_from)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                "default",
                value,
                kind,
                vec(vector_of),
                "current",
                3,
                1.0,
                index,
                family,
                "2026-10-18T00:00:00+00:00",
                "2026-10-18T00:00:00+00:00",
                f"2026-10-1{index}T00:00:00+00:00",
            ),
        )
    conn.commit()
    conn.close()


def seed_edges(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    for key, value in (
        ("award.onia-2026.top-60", "Top 60 of the ONIA 2026 contest."),
        ("memo", "The memo key itself."),
        ("memo.http.fastapi", "The memo HTTP layer."),
    ):
        conn.execute(
            "INSERT INTO memories(key, namespace, value, type, embedding, status, stability, score, uses,"
            " family_id, created_at, updated_at, valid_from)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, "default", value, "context", vec(value), "current", 3, 1.0, 0, "fam-award",
             "2026-10-18T00:00:00+00:00", "2026-10-18T00:00:00+00:00", "2026-10-18T00:00:00+00:00"))
    for src, dst, weight in (
        ("preference.frontend-stack-core", "preference", 0.5),
        ("preference.frontend-stack-core", "preference.frontend-stack-motion", 0.7),
        ("award.onia-2026.top-60", "award", 0.5),
        ("memo.http.fastapi", "memo", 0.6),
    ):
        conn.execute(
            "INSERT INTO edges(src_key,dst_key,rel,namespace,weight,created_at) VALUES(?,?,?,?,?,?)",
            (src, dst, "mentions", "default", weight, "2026-10-18T00:00:00+00:00"))
    conn.commit()
    conn.close()


def counts(path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(str(path))
    edges = conn.execute("SELECT count(*) FROM edges").fetchone()[0]
    aliases = conn.execute("SELECT count(*) FROM aliases").fetchone()[0]
    conn.close()
    return edges, aliases


def run(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )


def test_backfill_derives_aliases_edges_and_is_idempotent(tmp_path):
    db = tmp_path / "memo.db"
    seed(db)

    dry = run(db)
    assert dry.returncode == 0, dry.stderr
    assert "dry run" in dry.stdout
    assert counts(db) == (0, 0), "a dry run must not write"

    first = run(db, "--apply")
    assert first.returncode == 0, first.stderr
    edges, aliases = counts(db)
    assert aliases >= 3, first.stdout
    assert edges >= 1, first.stdout

    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT canonical FROM aliases WHERE segment = 'design-preferences-for-mansa-ui'"
    ).fetchone() == ("Design preferences for Mansa UI",)
    weights = [row[0] for row in conn.execute("SELECT weight FROM edges WHERE rel = 'mentions'")]
    assert weights and all(weight != 1.0 for weight in weights), weights
    assert conn.execute("SELECT count(*) FROM edges WHERE rel = 'derived_from'").fetchone()[0] >= 1
    conn.close()

    second = run(db, "--apply")
    assert second.returncode == 0, second.stderr
    assert counts(db) == (edges, aliases), "a second --apply must change nothing"


def test_prune_drops_prefix_edges_and_declares_the_rest(tmp_path):
    db = tmp_path / "memo.db"
    seed(db)
    seed_edges(db)

    dry = run(db, "--prune")
    assert dry.returncode == 0, dry.stderr
    assert "dry run" in dry.stdout
    assert "2 to delete" in dry.stdout, dry.stdout
    assert "2 to add" in dry.stdout, dry.stdout
    assert "first segment of src" in dry.stdout, dry.stdout
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT count(*) FROM edges").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM edge_declarers").fetchone()[0] == 0
    conn.close()

    first = run(db, "--prune", "--apply")
    assert first.returncode == 0, first.stderr
    assert "pruned 2 prefix edges" in first.stdout, first.stdout
    assert "added 2 declarer rows" in first.stdout, first.stdout
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT src_key,dst_key,rel,namespace,weight,created_at FROM edges"
        " ORDER BY src_key, dst_key").fetchall()
    assert rows == [
        ("memo.http.fastapi", "memo", "mentions", "default", 0.6, "2026-10-18T00:00:00+00:00"),
        ("preference.frontend-stack-core", "preference.frontend-stack-motion", "mentions",
         "default", 0.7, "2026-10-18T00:00:00+00:00"),
    ], rows
    declarers = conn.execute(
        "SELECT src_key,dst_key,rel,namespace,declarers,status,evidence,first_seen,last_seen"
        " FROM edge_declarers ORDER BY src_key, dst_key").fetchall()
    assert declarers == [
        ("memo.http.fastapi", "memo", "mentions", "default", "backfill:legacy", "candidate", 1,
         "2026-10-18T00:00:00+00:00", "2026-10-18T00:00:00+00:00"),
        ("preference.frontend-stack-core", "preference.frontend-stack-motion", "mentions",
         "default", "backfill:legacy", "candidate", 1,
         "2026-10-18T00:00:00+00:00", "2026-10-18T00:00:00+00:00"),
    ], declarers
    conn.close()

    second = run(db, "--prune", "--apply")
    assert second.returncode == 0, second.stderr
    assert "pruned 0 prefix edges" in second.stdout, second.stdout
    assert "added 0 declarer rows" in second.stdout, second.stdout
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT count(*) FROM edges").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM edge_declarers").fetchone()[0] == 2
    conn.close()
