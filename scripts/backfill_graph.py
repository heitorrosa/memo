"""Backfill edges and aliases for memories saved before the graph writer existed.

Rows written by the older migration path never ran `record_edges`, so most
memories are "bare": no aliases, and the only edges are the ones the MCP clients
declared by hand.

Dry run by default. Point it at a COPY of the DB, or stop the memo container
first: on a Windows bind mount a host write while the container holds memo.db
open breaks the container's SQLite handle, and every write endpoint then answers
500 "unable to open database file".

    python scripts/backfill_graph.py data/memo.db            # report only
    python scripts/backfill_graph.py data/memo.db --apply    # write

Idempotent: a second --apply adds nothing (the writers ignore what exists).
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memo.db import connect as db_connect  # noqa: E402
from memo.embeddings import cosine  # noqa: E402
from memo.memory.graph import record_alias, record_link  # noqa: E402

TOP_K = 3  # neighbours per memory, mirroring the read-time layer in server/api.py
FLOOR = 0.5  # cosine below this is noise, not a relationship
CAP = 400  # hard ceiling on materialised similarity edges
ALIAS_DECLARER = "backfill:key"
LINK_DECLARER = "backfill:similarity"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fetch(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def plan_aliases(memories: list[dict], existing: dict[tuple[str, str], str]) -> list[tuple[str, str, str]]:
    """(namespace, segment, canonical) for keys that lack one."""
    out: list[tuple[str, str, str]] = []
    by_ns: dict[str, list[dict]] = {}
    for m in memories:
        by_ns.setdefault(m["namespace"], []).append(m)
    for ns, items in sorted(by_ns.items()):
        tails: dict[str, int] = {}
        for m in items:
            if "." in m["key"]:
                tail = m["key"].rsplit(".", 1)[1]
                tails[tail] = tails.get(tail, 0) + 1
        for m in items:
            key = m["key"]
            if m["status"] != "current":
                continue
            if "." in key:
                tail = key.rsplit(".", 1)[1]
                if tails.get(tail, 0) != 1:  # ambiguous shorthand
                    continue
                segment = tail
            else:
                segment = slugify(key)
            if not segment or segment == key:
                continue
            known = existing.get((ns, segment))
            if known is not None:  # already maps somewhere
                continue
            out.append((ns, segment, key))
    return out


def plan_similarity(memories: list[dict]) -> list[tuple[str, str, str, float]]:
    """(namespace, src, dst, score) top-K per memory, undirected, capped."""
    with_vectors = [m for m in memories if m["embedding"]]
    scores: dict[tuple[str, str, str], tuple[str, str, float]] = {}
    for i, a in enumerate(with_vectors):
        ranked: list[tuple[float, dict]] = []
        for b in with_vectors[i + 1:]:
            if b["namespace"] != a["namespace"]:
                continue
            score = cosine(a["embedding"], b["embedding"])
            if score >= FLOOR:
                ranked.append((score, b))
        ranked.sort(key=lambda pair: -pair[0])
        for score, b in ranked:
            for src, dst in ((a, b), (b, a)):
                key = (src["namespace"], src["key"], dst["key"])
                pair = (src["namespace"], src["key"], dst["key"], score)
                if key in scores and scores[key][2] >= score:
                    continue
                scores[key] = pair
    # keep the top-K neighbours of each node, then dedupe the undirected pair
    best: dict[tuple[str, str, str], tuple[str, str, str, float]] = {}
    for (ns, src, dst), pair in scores.items():
        un = (ns, *sorted((src, dst)))
        if un not in best or best[un][3] < pair[3]:
            best[un] = pair
    ranked_pairs = sorted(best.values(), key=lambda p: (-p[3], p[1], p[2]))
    per_node: dict[tuple[str, str], int] = {}
    kept: list[tuple[str, str, str, float]] = []
    for ns, src, dst, score in ranked_pairs:
        if len(kept) >= CAP:
            break
        a = per_node.get((ns, src), 0)
        b = per_node.get((ns, dst), 0)
        if a >= TOP_K or b >= TOP_K:
            continue
        per_node[(ns, src)] = a + 1
        per_node[(ns, dst)] = b + 1
        kept.append((ns, src, dst, score))
    return kept


def plan_families(memories: list[dict]) -> list[tuple[str, str, str]]:
    """(namespace, src, dst) chaining revisions of one family oldest -> newest."""
    families: dict[tuple[str, str], list[dict]] = {}
    for m in memories:
        if m["family_id"]:
            families.setdefault((m["namespace"], m["family_id"]), []).append(m)
    out: list[tuple[str, str, str]] = []
    for (ns, _), items in sorted(families.items()):
        if len(items) < 2:
            continue
        ordered = sorted(items, key=lambda m: (m["valid_from"] or "", m["key"]))
        for prev, cur in zip(ordered, ordered[1:]):
            out.append((ns, cur["key"], prev["key"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db", help="path to memo.db (use a copy, or stop the container)")
    ap.add_argument("--apply", action="store_true", help="write; default is a dry run")
    args = ap.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"no such db: {path}")
        return 2

    conn = db_connect(str(path))
    conn.row_factory = sqlite3.Row
    memories = fetch(
        conn,
        "SELECT key, namespace, status, family_id, valid_from, embedding FROM memories",
    )
    existing = {
        (row["namespace"], row["segment"]): row["canonical"]
        for row in fetch(conn, "SELECT namespace, segment, canonical FROM aliases")
    }
    edges_before = fetch(conn, "SELECT COUNT(*) AS n FROM edges")[0]["n"]

    aliases = plan_aliases(memories, existing)
    similar = plan_similarity(memories)
    families = plan_families(memories)

    print(f"db            {path}")
    print(f"memories      {len(memories)} ({len({m['namespace'] for m in memories})} namespaces)")
    print(f"edges before  {edges_before}  aliases before {len(existing)}")
    print(f"aliases       {len(aliases)} to write")
    print(f"similarity    {len(similar)} pairs (top {TOP_K}, score >= {FLOOR}, cap {CAP})")
    print(f"family chains {len(families)} revision links")

    if not args.apply:
        print("dry run: nothing written (pass --apply)")
        for ns, segment, canonical in aliases[:5]:
            print(f"  alias  {ns}: {segment} -> {canonical}")
        for ns, src, dst, score in similar[:5]:
            print(f"  edge   {ns}: {src} -{score:.3f}-> {dst}")
        return 0

    # The writers run through peewee on their own connection to the same file, so
    # no outer transaction is held here: an open write lock would make them wait
    # out busy_timeout and fail with "database is locked".
    for ns, segment, canonical in aliases:
        record_alias(conn, segment, canonical, ALIAS_DECLARER, ns)
    for ns, src, dst, score in similar:
        record_link(conn, src, dst, "mentions", LINK_DECLARER, ns)
        conn.execute(
            "UPDATE edges SET weight=? WHERE src_key=? AND dst_key=? AND rel='mentions' AND namespace=?",
            (score, src, dst, ns),
        )
        conn.commit()
    for ns, src, dst in families:
        record_link(conn, src, dst, "derived_from", LINK_DECLARER, ns)
    conn.commit()

    after = fetch(conn, "SELECT COUNT(*) AS n FROM edges")[0]["n"]
    aliases_after = fetch(conn, "SELECT COUNT(*) AS n FROM aliases")[0]["n"]
    by_rel = fetch(conn, "SELECT rel, COUNT(*) AS n FROM edges GROUP BY rel ORDER BY rel")
    print(f"applied: edges {edges_before} -> {after}, aliases {len(existing)} -> {aliases_after}")
    print("edges by rel: " + ", ".join(f"{r['rel']} {r['n']}" for r in by_rel))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
