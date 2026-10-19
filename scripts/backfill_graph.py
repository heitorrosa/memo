from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memo.db import connect as db_connect
from memo.embeddings import cosine
from memo.memory.graph import record_alias, record_link

TOP_K = 3
FLOOR = 0.5
CAP = 400
ALIAS_DECLARER = "backfill:key"
LINK_DECLARER = "backfill:similarity"
LEGACY_DECLARER = "backfill:legacy"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fetch(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def plan_aliases(memories: list[dict], existing: dict[tuple[str, str], str]) -> list[tuple[str, str, str]]:
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
                if tails.get(tail, 0) != 1:
                    continue
                segment = tail
            else:
                segment = slugify(key)
            if not segment or segment == key:
                continue
            known = existing.get((ns, segment))
            if known is not None:
                continue
            out.append((ns, segment, key))
    return out


def plan_similarity(memories: list[dict]) -> list[tuple[str, str, str, float]]:
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


def plan_prune(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    prefixes = fetch(
        conn,
        "SELECT src_key, dst_key, rel, namespace FROM edges e"
        " WHERE instr(e.src_key, '.') > 0"
        " AND e.dst_key = substr(e.src_key, 1, instr(e.src_key, '.') - 1)"
        " AND NOT EXISTS (SELECT 1 FROM memories m WHERE m.key = e.dst_key"
        " AND m.namespace = e.namespace)")
    dropped = {(row["src_key"], row["dst_key"], row["rel"], row["namespace"])
               for row in prefixes}
    missing = [
        row for row in fetch(
            conn,
            "SELECT e.src_key, e.dst_key, e.rel, e.namespace, e.created_at"
            " FROM edges e WHERE NOT EXISTS (SELECT 1 FROM edge_declarers d"
            " WHERE d.src_key = e.src_key AND d.dst_key = e.dst_key"
            " AND d.rel = e.rel AND d.namespace = e.namespace)")
        if (row["src_key"], row["dst_key"], row["rel"], row["namespace"]) not in dropped
    ]
    return prefixes, missing


def apply_prune(conn: sqlite3.Connection, prefixes: list[dict],
                missing: list[dict]) -> None:
    for row in prefixes:
        conn.execute(
            "DELETE FROM edge_declarers WHERE src_key=? AND dst_key=? AND rel=? AND namespace=?",
            (row["src_key"], row["dst_key"], row["rel"], row["namespace"]))
        conn.execute(
            "DELETE FROM edges WHERE src_key=? AND dst_key=? AND rel=? AND namespace=?",
            (row["src_key"], row["dst_key"], row["rel"], row["namespace"]))
    for row in missing:
        conn.execute(
            "INSERT OR IGNORE INTO edge_declarers(src_key,dst_key,rel,namespace,declarers,"
            "status,evidence,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?)",
            (row["src_key"], row["dst_key"], row["rel"], row["namespace"],
             LEGACY_DECLARER, "candidate", 1, row["created_at"], row["created_at"]))
    conn.commit()


def run_prune(conn: sqlite3.Connection, path: Path, apply: bool) -> int:
    prefixes, missing = plan_prune(conn)
    edges_before = fetch(conn, "SELECT COUNT(*) AS n FROM edges")[0]["n"]
    aliases_before = fetch(conn, "SELECT COUNT(*) AS n FROM aliases")[0]["n"]
    print(f"db            {path}")
    print(f"edges before  {edges_before}  aliases before {aliases_before}")
    print(f"prefix edges  {len(prefixes)} to delete"
          " (dst is the first segment of src and is not a memory key)")
    print(f"declarers     {len(missing)} to add")
    if not apply:
        print("dry run: nothing written (pass --apply)")
        for row in prefixes[:5]:
            print(f"  drop   {row['namespace']}: {row['src_key']} -> {row['dst_key']}")
        for row in missing[:5]:
            print(f"  decl   {row['namespace']}: {row['src_key']} -> {row['dst_key']} ({row['rel']})")
        return 0
    apply_prune(conn, prefixes, missing)
    edges_after = fetch(conn, "SELECT COUNT(*) AS n FROM edges")[0]["n"]
    aliases_after = fetch(conn, "SELECT COUNT(*) AS n FROM aliases")[0]["n"]
    print(f"applied: edges {edges_before} -> {edges_after}, aliases {aliases_before} -> {aliases_after}")
    print(f"pruned {len(prefixes)} prefix edges, added {len(missing)} declarer rows")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="path to memo.db (use a copy, or stop the container)")
    ap.add_argument("--apply", action="store_true", help="write; default is a dry run")
    ap.add_argument("--prune", action="store_true",
                    help="drop the edges whose dst is the first segment of the src and is not a "
                         "memory key, and declare the rest")
    args = ap.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"no such db: {path}")
        return 2

    conn = db_connect(str(path))
    conn.row_factory = sqlite3.Row

    if args.prune:
        code = run_prune(conn, path, args.apply)
        conn.close()
        return code

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
