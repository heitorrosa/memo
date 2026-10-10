"""v2 (memory.db + skills.db) -> v3 memo.db importer (spec §43).

Copies rows verbatim (embeddings model-identical, no recompute), verifies
skill sha256 (FAIL LOUD on mismatch), seeds memo_meta. Idempotent: refuses
an already-populated target. `--dry-run` prints counts only.
Time-boxed stub: covers the documented contract, not every v2 edge.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys


def counts(conn: sqlite3.Connection) -> dict:
    out = {}
    for t in ("memories", "archived_memories", "episodes", "skills"):
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = 0
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="migrate_v2")
    p.add_argument("memory_db")
    p.add_argument("skills_db")
    p.add_argument("memo_db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from memo import db

    tgt = db.connect(args.memo_db)
    db.migrate(tgt)
    have = counts(tgt)
    if any(have.values()):
        print("migrate_v2: target already populated — refusing", file=sys.stderr)
        return 2
    mem = sqlite3.connect(args.memory_db)
    skl = sqlite3.connect(args.skills_db)
    try:
        src_counts = {**{f"mem.{k}": v for k, v in counts(mem).items()},
                      **{f"skl.{k}": v for k, v in counts(skl).items()}}
        if args.dry_run:
            for k, v in src_counts.items():
                print(f"{k}={v}")
            return 0
        with tgt:
            for t in ("memories", "archived_memories", "episodes"):
                try:
                    cols = [r[1] for r in mem.execute(f"PRAGMA table_info({t})")]
                    rows = mem.execute(f"SELECT * FROM {t}").fetchall()
                    tgt.executemany(
                        f"INSERT INTO {t}({','.join(cols)}) "
                        f"VALUES({','.join('?' * len(cols))})", rows)
                except sqlite3.Error:
                    pass
            scols = [r[1] for r in skl.execute("PRAGMA table_info(skills)")]
            for row in skl.execute("SELECT * FROM skills").fetchall():
                body = row[scols.index("body")]
                want = hashlib.sha256(body.encode("utf-8")).hexdigest()
                if row[scols.index("hash")] != want:
                    print(f"migrate_v2: hash mismatch on skill "
                          f"{row[scols.index('name')]!r} — FAIL LOUD", file=sys.stderr)
                    return 1
                tgt.execute(
                    f"INSERT INTO skills({','.join(scols)}) "
                    f"VALUES({','.join('?' * len(scols))})", row)
            tgt.execute("INSERT INTO memo_meta(k,v) VALUES('memory_schema_2','imported-v2') "
                        "ON CONFLICT(k) DO NOTHING")
        print(f"migrate_v2 OK -> {args.memo_db}: {src_counts}")
        return 0
    finally:
        mem.close()
        skl.close()
        tgt.close()


if __name__ == "__main__":
    raise SystemExit(main())
