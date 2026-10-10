"""Bounded janitor (spec §§16-17,23). Runs at the END of memory.save /
skills.sync tails; scheduler-daily hits the same function (no 2nd path).

Import rule: ONLY temporal (+stdlib/sqlite) — never memory/skills (tails
call IN here via lazy import, so this module must not import them back).
"""
from __future__ import annotations

import math
import os
import sqlite3
import sys

from memo import temporal

CAP = 250
GRACE_DAYS = 1.0
CRON_SCORE, CRON_USES, CRON_AGE = 0.1, 0, 90.0
ROLLUP_AGE, TOMBSTONE_AGE = 30.0, 30.0

ARCHIVE_COLUMNS = ("key,namespace,value,type,embedding,stability,score,uses,"
              "created_at,updated_at,valid_from,valid_to,status,family_id,"
              "superseded_by,relation_type")


def age_days(created_at: str, now) -> float | None:
    try:
        return max(0.0, (now - temporal.parse_ts(created_at))
                   .total_seconds() / 86400)
    except ValueError:
        return None


def touched_after(updated_at: str, t0) -> bool:
    """M-024: rows touched in the current call are never swept by it."""
    try:
        return temporal.parse_ts(updated_at) >= t0
    except ValueError:
        return False


def archive_row(conn, key, namespace, now_iso, reason):
    row = conn.execute(
        f"SELECT {ARCHIVE_COLUMNS} FROM memories WHERE key=? AND namespace=?",
        (key, namespace)).fetchone()
    if row is None:
        return False
    with conn:
        conn.execute(
            f"INSERT INTO archived_memories({ARCHIVE_COLUMNS},archived_at,"
            f"archived_reason) VALUES({','.join('?' for _ in range(16))},?,?)",
            (*row, now_iso, reason))
        conn.execute("DELETE FROM memories WHERE key=? AND namespace=?",
                     (key, namespace))
    return True


def pick_victim(conn, namespace, now) -> str | None:
    """argmin (uses/max(age,1))*exp(-age/S) over grace-passing currents."""
    best, best_energy = None, None
    for key, uses, stability, created_at, updated_at in conn.execute(
            "SELECT key, uses, stability, created_at, updated_at FROM memories"
            " WHERE namespace=? AND status='current'", (namespace,)):
        age = age_days(created_at, now)
        if age is None or age < GRACE_DAYS or touched_after(updated_at, now):
            continue
        energy = (uses / max(age, 1.0)) * math.exp(-age / stability)
        if best_energy is None or energy < best_energy or (energy == best_energy and (created_at, key) < best[1]):
            best, best_energy = (key, (created_at, key)), energy
    return best[0] if best else None


def run(conn: sqlite3.Connection, max_moves: int = 5,
        now: str | None = None) -> dict:
    """One bounded pass; idempotent. Returns
    {evicted: key|None, archived: [keys], rolled_up: episodes_created}."""
    sweep_time = temporal.parse_ts(now) if now else temporal.parse_ts(
        temporal.now_iso())
    now_iso = temporal.now_iso()
    evicted, archived, rolled = None, [], 0
    moves = [max_moves]  # mutable budget shared across steps, oldest-first

    def take() -> bool:
        if moves[0] <= 0:
            return False
        moves[0] -= 1
        return True

    # (1) over-cap loop, per namespace (cap counts current-only, M-006).
    for (namespace,) in conn.execute(
            "SELECT DISTINCT namespace FROM memories").fetchall():
        while take():
            count = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE namespace=?"
                " AND status='current'", (namespace,)).fetchone()[0]
            if count <= CAP:
                moves[0] += 1  # take() only spent when we actually evict
                break
            victim = pick_victim(conn, namespace, sweep_time)
            if victim is None:
                moves[0] += 1
                break  # all grace-guarded: converge on later calls
            archive_row(conn, victim, namespace, now_iso, "cap_eviction")
            archived.append(victim)
            if evicted is None:
                evicted = victim

    # (2) cron triple-AND sweep; prefs/feedback EXEMPT (M-016).
    if moves[0] > 0:
        rows = conn.execute(
            "SELECT key, namespace, created_at, updated_at FROM memories"
            " WHERE status='current' AND type IN ('analysis','context')"
            " ORDER BY created_at").fetchall()
        for key, namespace, created_at, updated_at in rows:
            if moves[0] <= 0:
                break
            age = age_days(created_at, sweep_time)
            if age is None or age <= CRON_AGE or touched_after(updated_at, sweep_time):
                continue
            score, uses = conn.execute(
                "SELECT score, uses FROM memories WHERE key=? AND namespace=?",
                (key, namespace)).fetchone()
            if score < CRON_SCORE and uses == CRON_USES:
                if take():
                    archive_row(conn, key, namespace, now_iso, "cron")
                    archived.append(key)

    # (3) context rollup >30d -> ONE episodes row (mechanical summary).
    if moves[0] > 0:
        rows = conn.execute(
            "SELECT key, namespace, value, created_at, updated_at FROM memories"
            " WHERE status='current' AND type='context'"
            " ORDER BY created_at").fetchall()
        due = [(key, namespace, value, created_at) for key, namespace, value, created_at, updated_at in rows
               if (age_days(created_at, sweep_time) or 0) > ROLLUP_AGE
               and not touched_after(updated_at, sweep_time)][:moves[0]]
        if due:
            for _ in due:
                take()
            with conn:
                conn.execute(
                    "INSERT INTO episodes(summary,source_count,rolled_up_at,"
                    "started_at,ended_at) VALUES(?,?,?,?,?)",
                    ("auto-rollup of %d context rows: " % len(due)
                     + " | ".join(value[:40] for _, _, value, _ in due),
                     len(due), now_iso,
                     min(created_at for _, _, _, created_at in due),
                     max(created_at for _, _, _, created_at in due)))
                for key, namespace, _, _ in due:
                    archive_row(conn, key, namespace, now_iso, "rollup")
                    archived.append(key)
            rolled = 1

    # (4) tombstone expiry >30d: drop the row (resurrection window closed).
    if moves[0] > 0:
        for (name, updated_at) in conn.execute(
                "SELECT name, updated_at FROM skills WHERE source='tombstone'"
                " ORDER BY updated_at").fetchall():
            if moves[0] <= 0:
                break
            try:
                age = max(0.0, (sweep_time - temporal.parse_ts(updated_at))
                          .total_seconds() / 86400)
            except ValueError:
                continue
            if age > TOMBSTONE_AGE and take():
                with conn:
                    conn.execute("DELETE FROM skills WHERE name=?", (name,))

    # ponytail: silent no-op runs; signal-only log keeps T-1..T-8 replay
    # readable (500+ saves) while preserving the per-action audit line.
    if (evicted or archived or rolled) or os.getenv("MEMO_LOG") == "debug":
        print(f"maint evicted={1 if evicted else 0} archived={len(archived)}"
              f" rolled={rolled}", file=sys.stderr)
    return {"evicted": evicted, "archived": archived, "rolled_up": rolled}
