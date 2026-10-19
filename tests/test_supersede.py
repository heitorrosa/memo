import sqlite3
import subprocess
import sys
from pathlib import Path

from memo.db import connect, migrate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "supersede.py"
STALE_KEY = "mansa.language-pass.python-lane"
CORRECTION_KEY = "correction.mansa.no-ptbr-strings-in-backend"
STALE_FROM = "2026-10-13T15:41:42+00:00"
CORRECTION_FROM = "2026-10-14T05:29:57+00:00"


def vec(text: str) -> bytes:
    import hashlib

    import numpy as np

    seed_value = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "little")
    generator = np.random.RandomState(seed_value)
    vector = generator.rand(384).astype(np.float32) - 0.5
    vector /= np.linalg.norm(vector) + 1e-12
    return vector.tobytes()


def seed(path: Path, keys: tuple[str, ...] = (STALE_KEY, CORRECTION_KEY)) -> None:
    conn = connect(str(path))
    migrate(conn)
    rows = [
        (STALE_KEY, "Python language pass: customer-facing API errors -> pt-BR.",
         "context", "fam-stale", STALE_FROM),
        (CORRECTION_KEY, "Never use PT-BR strings in the backend. Correction 2026-10-14.",
         "preference", "fam-correction", CORRECTION_FROM),
    ]
    for key, value, kind, family, valid_from in rows:
        if key not in keys:
            continue
        conn.execute(
            "INSERT INTO memories(key, namespace, value, type, embedding, status, stability, score, uses,"
            " family_id, created_at, updated_at, valid_from)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, "default", value, kind, vec(value), "current", 3, 1.0, 0, family,
             valid_from, valid_from, valid_from),
        )
    conn.commit()
    conn.close()


def state(path: Path) -> tuple:
    conn = sqlite3.connect(str(path))
    stale = conn.execute(
        "SELECT status, family_id, valid_to, superseded_by FROM memories WHERE key=?",
        (STALE_KEY,)).fetchone()
    correction = conn.execute(
        "SELECT status, family_id, relation_type FROM memories WHERE key=?",
        (CORRECTION_KEY,)).fetchone()
    edges = conn.execute(
        "SELECT src_key, dst_key, rel, weight FROM edges WHERE rel='contradicts'"
    ).fetchall()
    declarers = conn.execute(
        "SELECT src_key, dst_key, rel, declarers, status, evidence FROM edge_declarers"
        " WHERE rel='contradicts'").fetchall()
    conn.close()
    return stale, correction, edges, declarers


def run(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )


def test_supersede_marks_the_stale_row_and_links_the_correction(tmp_path):
    db = tmp_path / "memo.db"
    seed(db)

    dry = run(db)
    assert dry.returncode == 0, dry.stderr
    assert "dry run" in dry.stdout
    assert "status=current" in dry.stdout
    assert "edge         none between the two" in dry.stdout
    stale, correction, edges, declarers = state(db)
    assert stale == ("current", "fam-stale", None, None), stale
    assert correction == ("current", "fam-correction", None), correction
    assert edges == []
    assert declarers == []

    first = run(db, "--apply")
    assert first.returncode == 0, first.stderr
    assert "applied" in first.stdout
    stale, correction, edges, declarers = state(db)
    assert stale == ("superseded", "fam-stale", CORRECTION_FROM, CORRECTION_KEY), stale
    assert correction == ("current", "fam-stale", "corrects"), correction
    assert edges == [(CORRECTION_KEY, STALE_KEY, "contradicts", 0.0)], edges
    assert declarers == [(CORRECTION_KEY, STALE_KEY, "contradicts",
                          "backfill:contradiction", "candidate", 1)], declarers

    second = run(db, "--apply")
    assert second.returncode == 0, second.stderr
    assert "nothing to do" in second.stdout
    assert state(db) == (stale, correction, edges, declarers)


def test_supersede_stops_when_the_correction_is_missing(tmp_path):
    db = tmp_path / "memo.db"
    seed(db, keys=(STALE_KEY,))

    dry = run(db)
    assert dry.returncode == 2, dry.stdout
    assert "stopped: missing memories" in dry.stdout
    assert "dry run" not in dry.stdout
    stale, correction, edges, declarers = state(db)
    assert stale == ("current", "fam-stale", None, None), stale
    assert correction is None
    assert edges == []
    assert declarers == []
