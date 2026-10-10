"""Bounded janitor (spec §§16-17,23). Runs at the END of memory.save /
skills.sync tails; scheduler-daily hits the same function (no 2nd path).

Import rule: ONLY temporal (+stdlib/sqlite) — never memory/skills (tails
call IN here via lazy import, so this module must not import them back).
"""
from __future__ import annotations

import math
import sqlite3
import sys

from memo import temporal

CAP = 250
GRACE_DAYS = 1.0
CRON_SCORE, CRON_USES, CRON_AGE = 0.1, 0, 90.0
ROLLUP_AGE, TOMBSTONE_AGE = 30.0, 30.0

_ARCH_COLS = ("key,namespace,value,type,embedding,stability,score,uses,"
              "created_at,updated_at,valid_from,valid_to,status,family_id,"
              "superseded_by,relation_type")


def _age_days(created_at: str, now) -> float | None:
    try:
        return max(0.0, (now - temporal.parse_ts(created_at))
                   .total_seconds() / 86400)
    except ValueError:
        return None


def _touched_after(updated_at: str, t0) -> bool:
    """M-024: rows touched in the current call are never swept by it."""
    try:
        return temporal.parse_ts(updated_at) >= t0
    except ValueError:
        return False


def _archive(conn, key, ns, now_iso, reason):
    row = conn.execute(
        f"SELECT {_ARCH_COLS} FROM memories WHERE key=? AND namespace=?",
        (key, ns)).fetchone()
    if row is None:
        return False
    with conn:
        conn.execute(
            f"INSERT INTO archived_memories({_ARCH_COLS},archived_at,"
            f"archived_reason) VALUES({','.join('?' for _ in range(16))},?,?)",
            (*row, now_iso, reason))
        conn.execute("DELETE FROM memories WHERE key=? AND namespace=?",
                     (key, ns))
    return True


def _victim(conn, ns, now) -> str | None:
    """argmin (uses/max(age,1))*exp(-age/S) over grace-passing currents."""
    best, best_e = None, None
    for key, uses, stab, ca, ua in conn.execute(
            "SELECT key, uses, stability, created_at, updated_at FROM memories"
            " WHERE namespace=? AND status='current'", (ns,)):
        age = _age_days(ca, now)
        if age is None or age < GRACE_DAYS or _touched_after(ua, now):
            continue
        e = (uses / max(age, 1.0)) * math.exp(-age / stab)
        if best_e is None or e < best_e or (e == best_e and (ca, key) < best[1]):
            best, best_e = (key, (ca, key)), e
    return best[0] if best else None


def run(conn: sqlite3.Connection, max_moves: int = 5,
        now: str | None = None) -> dict:
    """One bounded pass; idempotent. Returns
    {evicted: key|None, archived: [keys], rolled_up: episodes_created}."""
    t0 = temporal.parse_ts(now) if now else temporal.parse_ts(
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
    for (ns,) in conn.execute(
            "SELECT DISTINCT namespace FROM memories").fetchall():
        while take():
            n = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE namespace=?"
                " AND status='current'", (ns,)).fetchone()[0]
            if n <= CAP:
                moves[0] += 1  # take() only spent when we actually evict
                break
            v = _victim(conn, ns, t0)
            if v is None:
                moves[0] += 1
                break  # all grace-guarded: converge on later calls
            _archive(conn, v, ns, now_iso, "cap_eviction")
            archived.append(v)
            if evicted is None:
                evicted = v

    # (2) cron triple-AND sweep; prefs/feedback EXEMPT (M-016).
    if moves[0] > 0:
        rows = conn.execute(
            "SELECT key, namespace, created_at, updated_at FROM memories"
            " WHERE status='current' AND type IN ('analysis','context')"
            " ORDER BY created_at").fetchall()
        for key, ns, ca, ua in rows:
            if moves[0] <= 0:
                break
            age = _age_days(ca, t0)
            if age is None or age <= CRON_AGE or _touched_after(ua, t0):
                continue
            score, uses = conn.execute(
                "SELECT score, uses FROM memories WHERE key=? AND namespace=?",
                (key, ns)).fetchone()
            if score < CRON_SCORE and uses == CRON_USES:
                if take():
                    _archive(conn, key, ns, now_iso, "cron")
                    archived.append(key)

    # (3) context rollup >30d -> ONE episodes row (mechanical summary).
    if moves[0] > 0:
        rows = conn.execute(
            "SELECT key, namespace, value, created_at, updated_at FROM memories"
            " WHERE status='current' AND type='context'"
            " ORDER BY created_at").fetchall()
        due = [(k, ns, v, ca) for k, ns, v, ca, ua in rows
               if (_age_days(ca, t0) or 0) > ROLLUP_AGE
               and not _touched_after(ua, t0)][:moves[0]]
        if due:
            for _ in due:
                take()
            with conn:
                conn.execute(
                    "INSERT INTO episodes(summary,source_count,rolled_up_at,"
                    "started_at,ended_at) VALUES(?,?,?,?,?)",
                    ("auto-rollup of %d context rows: " % len(due)
                     + " | ".join(v[:40] for _, _, v, _ in due),
                     len(due), now_iso,
                     min(ca for _, _, _, ca in due),
                     max(ca for _, _, _, ca in due)))
                for k, ns, _, _ in due:
                    _archive(conn, k, ns, now_iso, "rollup")
                    archived.append(k)
            rolled = 1

    # (4) tombstone expiry >30d: drop the row (resurrection window closed).
    if moves[0] > 0:
        for (name, ua) in conn.execute(
                "SELECT name, updated_at FROM skills WHERE source='tombstone'"
                " ORDER BY updated_at").fetchall():
            if moves[0] <= 0:
                break
            try:
                age = max(0.0, (t0 - temporal.parse_ts(ua))
                          .total_seconds() / 86400)
            except ValueError:
                continue
            if age > TOMBSTONE_AGE and take():
                with conn:
                    conn.execute("DELETE FROM skills WHERE name=?", (name,))

    print(f"maint evicted={1 if evicted else 0} archived={len(archived)}"
          f" rolled={rolled}", file=sys.stderr)
    return {"evicted": evicted, "archived": archived, "rolled_up": rolled}
