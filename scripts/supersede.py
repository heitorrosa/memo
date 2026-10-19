from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memo import db as memo_db
from memo.db import connect as db_connect
from memo.memory.graph import record_link
from memo.models import orm as orm_models
from memo.models.orm import Memory as OrmMemory

STALE_KEY = "mansa.language-pass.python-lane"
CORRECTION_KEY = "correction.mansa.no-ptbr-strings-in-backend"
NAMESPACE = "default"
REL = "contradicts"
DECLARER = "backfill:contradiction"


def fetch(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def memory_row(conn: sqlite3.Connection, key: str) -> dict | None:
    rows = fetch(
        conn,
        "SELECT key, namespace, type, status, family_id, relation_type,"
        " valid_from, valid_to, superseded_by FROM memories"
        " WHERE key=? AND namespace=?",
        (key, NAMESPACE),
    )
    return rows[0] if rows else None


def link_rows(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    edges = fetch(
        conn,
        "SELECT src_key, dst_key, rel, weight FROM edges"
        " WHERE namespace=? AND src_key IN (?,?) AND dst_key IN (?,?)",
        (NAMESPACE, CORRECTION_KEY, STALE_KEY, CORRECTION_KEY, STALE_KEY),
    )
    declarers = fetch(
        conn,
        "SELECT src_key, dst_key, rel, declarers, status, evidence FROM edge_declarers"
        " WHERE namespace=? AND src_key IN (?,?) AND dst_key IN (?,?)",
        (NAMESPACE, CORRECTION_KEY, STALE_KEY, CORRECTION_KEY, STALE_KEY),
    )
    return edges, declarers


def build_plan(conn: sqlite3.Connection) -> dict:
    stale = memory_row(conn, STALE_KEY)
    correction = memory_row(conn, CORRECTION_KEY)
    edges, declarers = link_rows(conn)
    problems: list[str] = []
    changes: list[str] = []
    plan = {"stale": stale, "correction": correction, "edges": edges,
            "declarers": declarers, "changes": changes, "problems": problems,
            "edge_missing": True}
    if stale is None or correction is None:
        missing = [key for key, row in ((STALE_KEY, stale), (CORRECTION_KEY, correction))
                   if row is None]
        problems.append("missing memories: " + ", ".join(missing))
        return plan
    if correction["status"] != "current":
        problems.append(f"{CORRECTION_KEY} status is {correction['status']!r},"
                        " expected 'current'")
    if (stale["status"] == "superseded"
            and stale["superseded_by"] not in (None, CORRECTION_KEY)):
        problems.append(f"{STALE_KEY} is already superseded by"
                        f" {stale['superseded_by']!r}")
    stale_target = {"status": "superseded", "valid_to": correction["valid_from"],
                    "superseded_by": CORRECTION_KEY}
    correction_target = {"family_id": stale["family_id"], "relation_type": "corrects"}
    for field, value in stale_target.items():
        if stale[field] != value:
            changes.append(f"{STALE_KEY}.{field}: {stale[field]!r} -> {value!r}")
    for field, value in correction_target.items():
        if correction[field] != value:
            changes.append(f"{CORRECTION_KEY}.{field}: {correction[field]!r} -> {value!r}")
    declared = any(row["rel"] == REL and DECLARER in (row["declarers"] or "").split(",")
                    for row in declarers)
    plan["edge_missing"] = not declared
    if plan["edge_missing"]:
        changes.append(f"edge: {CORRECTION_KEY} -{REL}-> {STALE_KEY}"
                       f" declarer={DECLARER} status=candidate")
    return plan


def apply(conn: sqlite3.Connection, plan: dict) -> None:
    stale = plan["stale"]
    correction = plan["correction"]
    peewee_db = memo_db.orm_db(conn)
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        (OrmMemory.update(status="superseded", valid_to=correction["valid_from"],
                          superseded_by=CORRECTION_KEY)
         .where((OrmMemory.key == STALE_KEY) & (OrmMemory.namespace == NAMESPACE))
         .execute())
        (OrmMemory.update(family_id=stale["family_id"], relation_type="corrects")
         .where((OrmMemory.key == CORRECTION_KEY) & (OrmMemory.namespace == NAMESPACE))
         .execute())
        if plan["edge_missing"]:
            record_link(conn, CORRECTION_KEY, STALE_KEY, REL, DECLARER, NAMESPACE)


def show(path: Path, plan: dict) -> None:
    print(f"db            {path}")
    for label in ("stale", "correction"):
        row = plan[label]
        if row is None:
            print(f"{label:<12} missing")
            continue
        print(f"{label:<12} {row['key']}")
        print(f"             status={row['status']} family_id={row['family_id']}"
              f" relation_type={row['relation_type']} valid_to={row['valid_to']}"
              f" superseded_by={row['superseded_by']}")
    if plan["edges"]:
        for edge in plan["edges"]:
            print(f"edge         {edge['src_key']} -{edge['rel']}-> {edge['dst_key']}"
                  f" weight={edge['weight']}")
    else:
        print("edge         none between the two")
    if plan["declarers"]:
        for declarer in plan["declarers"]:
            print(f"declarer     {declarer['src_key']} -{declarer['rel']}->"
                  f" {declarer['dst_key']} {declarer['declarers']}"
                  f" status={declarer['status']} evidence={declarer['evidence']}")
    else:
        print("declarer     none between the two")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="path to memo.db (stop the container before writing)")
    ap.add_argument("--apply", action="store_true", help="write; default is a dry run")
    args = ap.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"no such db: {path}")
        return 2

    conn = db_connect(str(path))
    plan = build_plan(conn)
    show(path, plan)

    if plan["problems"]:
        print("stopped: " + "; ".join(plan["problems"]))
        conn.close()
        return 2

    if not plan["changes"]:
        print("nothing to do: the supersession is already recorded")
        conn.close()
        return 0

    print(f"changes ({len(plan['changes'])}):")
    for change in plan["changes"]:
        print(f"  {change}")

    if not args.apply:
        print("dry run: nothing written (pass --apply)")
        conn.close()
        return 0

    apply(conn, plan)
    after = build_plan(conn)
    show(path, after)
    print("applied" if not after["changes"] else "applied, still pending: "
          + "; ".join(after["changes"]))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
