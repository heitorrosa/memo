"""The graph backfill derives aliases and edges from a bare DB, and is idempotent.

Run: `pytest tests/test_backfill.py -q` with the repo pythonpath on src.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

from memo.db import connect, migrate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backfill_graph.py"


def _vec(text: str) -> bytes:
    """Same shape as memo.embeddings.fallback_vectors: a unit float32 vector."""
    import hashlib

    import numpy as np

    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "little")
    generator = np.random.RandomState(seed)
    vector = generator.rand(384).astype(np.float32) - 0.5
    vector /= np.linalg.norm(vector) + 1e-12
    return vector.tobytes()


def _seed(path: Path) -> None:
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
                # the first two share a vector so the cosine clears the 0.5 floor
                _vec(vector_of),
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


def _counts(path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(str(path))
    edges = conn.execute("SELECT count(*) FROM edges").fetchone()[0]
    aliases = conn.execute("SELECT count(*) FROM aliases").fetchone()[0]
    conn.close()
    return edges, aliases


def _run(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )


def test_backfill_derives_aliases_edges_and_is_idempotent(tmp_path):
    db = tmp_path / "memo.db"
    _seed(db)

    dry = _run(db)
    assert dry.returncode == 0, dry.stderr
    assert "dry run" in dry.stdout
    assert _counts(db) == (0, 0), "a dry run must not write"

    first = _run(db, "--apply")
    assert first.returncode == 0, first.stderr
    edges, aliases = _counts(db)
    assert aliases >= 3, first.stdout
    assert edges >= 1, first.stdout

    conn = sqlite3.connect(str(db))
    # sentence key -> slug alias
    assert conn.execute(
        "SELECT canonical FROM aliases WHERE segment = 'design-preferences-for-mansa-ui'"
    ).fetchone() == ("Design preferences for Mansa UI",)
    # similarity edge carries the cosine score, not the writer's default 1.0
    weights = [row[0] for row in conn.execute("SELECT weight FROM edges WHERE rel = 'mentions'")]
    assert weights and all(weight != 1.0 for weight in weights), weights
    # family chain
    assert conn.execute("SELECT count(*) FROM edges WHERE rel = 'derived_from'").fetchone()[0] >= 1
    conn.close()

    second = _run(db, "--apply")
    assert second.returncode == 0, second.stderr
    assert _counts(db) == (edges, aliases), "a second --apply must change nothing"
