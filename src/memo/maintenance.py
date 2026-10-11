from __future__ import annotations

import math
import os
import sqlite3
import sys

from memo import db as memo_db
from memo import temporal
from memo.models import orm as orm_models
from memo.models.orm import ArchivedMemory as OrmArchived
from memo.models.orm import Episode as OrmEpisode
from memo.models.orm import Memory as OrmMemory
from memo.models.orm import Skill as OrmSkill

CAP = 2000
GRACE_DAYS = 1.0
CRON_SCORE, CRON_USES, CRON_AGE = 0.1, 0, 90.0
ROLLUP_AGE, TOMBSTONE_AGE = 30.0, 30.0


def age_days(created_at: str, now) -> float | None:
    try:
        return max(0.0, (now - temporal.parse_ts(created_at))
                   .total_seconds() / 86400)
    except ValueError:
        return None


def touched_after(updated_at: str, t0) -> bool:
    try:
        return temporal.parse_ts(updated_at) >= t0
    except ValueError:
        return False


def archive_row(conn, key, namespace, now_iso, reason):
    peewee_db = memo_db.orm_db(conn)
    with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
        row = OrmMemory.get_or_none(
            (OrmMemory.key == key) & (OrmMemory.namespace == namespace))
        if row is None:
            return False
        embedding = row.embedding
        if isinstance(embedding, (memoryview, bytearray)):
            embedding = bytes(embedding)
        OrmArchived.create(
            key=row.key, namespace=row.namespace, value=row.value,
            type=row.type, embedding=embedding, stability=row.stability,
            score=row.score, uses=row.uses, created_at=row.created_at,
            updated_at=row.updated_at, valid_from=row.valid_from,
            valid_to=row.valid_to, valid_until=row.valid_until,
            agent_id=row.agent_id, status=row.status,
            family_id=row.family_id, superseded_by=row.superseded_by,
            relation_type=row.relation_type,
            archived_at=now_iso, archived_reason=reason)
        (OrmMemory.delete()
         .where((OrmMemory.key == key) & (OrmMemory.namespace == namespace))
         .execute())
    return True


def pick_victim(conn, namespace, now) -> str | None:
    with memo_db.orm_ctx(conn):
        rows = list((OrmMemory.select(
            OrmMemory.key, OrmMemory.uses, OrmMemory.stability,
            OrmMemory.created_at, OrmMemory.updated_at)
            .where((OrmMemory.namespace == namespace)
                   & (OrmMemory.status == "current")).tuples()))
    best, best_energy = None, None
    for key, uses, stability, created_at, updated_at in rows:
        age = age_days(created_at, now)
        if age is None or age < GRACE_DAYS or touched_after(updated_at, now):
            continue
        energy = (uses / max(age, 1.0)) * math.exp(-age / stability)
        if best_energy is None or energy < best_energy or (energy == best_energy and (created_at, key) < best[1]):
            best, best_energy = (key, (created_at, key)), energy
    return best[0] if best else None


def run(conn: sqlite3.Connection, max_moves: int = 5,
        now: str | None = None) -> dict:
    sweep_time = temporal.parse_ts(now) if now else temporal.parse_ts(
        temporal.now_iso())
    now_iso = temporal.now_iso()
    evicted, archived, rolled = None, [], 0
    moves = [max_moves]

    def take() -> bool:
        if moves[0] <= 0:
            return False
        moves[0] -= 1
        return True

    peewee_db = memo_db.orm_db(conn)
    with memo_db.orm_ctx(conn):
        namespaces = [row.namespace for row in
                      OrmMemory.select(OrmMemory.namespace).distinct()]
    for namespace in namespaces:
        while take():
            with memo_db.orm_ctx(conn):
                count = (OrmMemory.select()
                         .where((OrmMemory.namespace == namespace)
                                & (OrmMemory.status == "current")).count())
            if count <= CAP:
                moves[0] += 1
                break
            victim = pick_victim(conn, namespace, sweep_time)
            if victim is None:
                moves[0] += 1
                break
            archive_row(conn, victim, namespace, now_iso, "cap_eviction")
            archived.append(victim)
            if evicted is None:
                evicted = victim

    if moves[0] > 0:
        with memo_db.orm_ctx(conn):
            rows = list((OrmMemory.select(
                OrmMemory.key, OrmMemory.namespace, OrmMemory.created_at,
                OrmMemory.updated_at, OrmMemory.score, OrmMemory.uses)
                .where((OrmMemory.status == "current")
                       & (OrmMemory.type.in_(["analysis", "context"])))
                .order_by(OrmMemory.created_at).tuples()))
        for key, namespace, created_at, updated_at, score, uses in rows:
            if moves[0] <= 0:
                break
            age = age_days(created_at, sweep_time)
            if age is None or age <= CRON_AGE or touched_after(updated_at, sweep_time):
                continue
            if score < CRON_SCORE and uses == CRON_USES:
                if take():
                    archive_row(conn, key, namespace, now_iso, "cron")
                    archived.append(key)

    if moves[0] > 0:
        with memo_db.orm_ctx(conn):
            rows = list((OrmMemory.select(
                OrmMemory.key, OrmMemory.namespace, OrmMemory.value,
                OrmMemory.created_at, OrmMemory.updated_at)
                .where((OrmMemory.status == "current")
                       & (OrmMemory.type == "context"))
                .order_by(OrmMemory.created_at).tuples()))
        due = [(key, namespace, value, created_at) for key, namespace, value, created_at, updated_at in rows
               if (age_days(created_at, sweep_time) or 0) > ROLLUP_AGE
               and not touched_after(updated_at, sweep_time)][:moves[0]]
        if due:
            for _ in due:
                take()
            with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
                OrmEpisode.create(
                    summary=("auto-rollup of %d context rows: " % len(due)
                             + " | ".join(value[:40] for _, _, value, _ in due)),
                    source_count=len(due), rolled_up_at=now_iso,
                    started_at=min(created_at for _, _, _, created_at in due),
                    ended_at=max(created_at for _, _, _, created_at in due))
            for key, namespace, _, _ in due:
                archive_row(conn, key, namespace, now_iso, "rollup")
                archived.append(key)
            rolled = 1

    if moves[0] > 0:
        with memo_db.orm_ctx(conn):
            tombs = list((OrmSkill.select(OrmSkill.name, OrmSkill.updated_at)
                          .where(OrmSkill.source == "tombstone")
                          .order_by(OrmSkill.updated_at).tuples()))
        for name, updated_at in tombs:
            if moves[0] <= 0:
                break
            try:
                age = max(0.0, (sweep_time - temporal.parse_ts(updated_at))
                          .total_seconds() / 86400)
            except ValueError:
                continue
            if age > TOMBSTONE_AGE and take():
                with peewee_db.bind_ctx(orm_models.MODELS), peewee_db.atomic():
                    (OrmSkill.delete().where(OrmSkill.name == name).execute())

    if (evicted or archived or rolled) or os.getenv("MEMO_LOG") == "debug":
        print(f"maint evicted={1 if evicted else 0} archived={len(archived)}"
              f" rolled={rolled}", file=sys.stderr)
    return {"evicted": evicted, "archived": archived, "rolled_up": rolled}
