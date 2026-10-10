"""Temporal classify + predicates + chain walk (spec §§9, 11, 32).

Import rule (§37): never imports skills/memory — pure logic + read queries.
"""

from __future__ import annotations

import datetime
import re
import sqlite3
import sys

OUTCOMES = ("unrelated", "qualifies", "extends", "contradicts", "reasserts")

REASSERT_PATTERN = re.compile(r"\b(as of \d{4}|still|confirm(?:s|ed|ing)?|remains?|continues? to)\b", re.I)
NEGATION_PATTERN = re.compile(
    r"\b(n['’]t\b|not\b|no longer\b|now\b|instead\b|switch(?:ed|ing)?\b|"
    r"rather than\b|prefer .* over\b|actually\b|changed?\b|used to\b)", re.I)
ADDITIVE_PATTERN = re.compile(r"\b(also\b|too\b|as well\b|in (?-i:[A-Z])|for (?:work|study|research|the scope)|some\b)", re.I)
CORRECTION_PATTERN = re.compile(r"\b(actually\b|correction\b|meant\b|was wrong\b|\d{4}-\d{2})", re.I)

STOP_WORDS = frozenset(
    "the a an in on of for to uses use using with some also still now as is are was were be been i my me".split())


def token_words(s: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z]+", s)} - STOP_WORDS


def has_overlap(a: str, b: str) -> bool:
    wa, wb = token_words(a), token_words(b)
    return bool(wa & wb)


def classify(old, new_key: str, new_value: str, judge=None) -> str:
    """Cheap-first 5-way classify. `old` is a row dict or a value string.

    Judge fires ONLY on the narrow negation path (M-009); a non-token
    judge output falls back to extends + stderr log (never crashes save).
    """
    old_value = old.get("value") if isinstance(old, dict) else str(old)
    old_s, new_s = old_value or "", new_value or ""
    if REASSERT_PATTERN.search(new_s):
        return "reasserts"
    if NEGATION_PATTERN.search(new_s) or NEGATION_PATTERN.search(old_s):
        if judge is not None:
            try:
                verdict = str(judge(old_s, new_key, new_s)).strip().lower()
            except Exception:
                verdict = ""
            if verdict in OUTCOMES:
                return verdict
            print(f"judge fallback: non-token output {verdict!r} -> extends",
                  file=sys.stderr)
        return "contradicts"
    if ADDITIVE_PATTERN.search(new_s):
        return "qualifies" if has_overlap(old_s, new_s) else "unrelated"
    return "extends"


def coerce_date(s: str) -> str:
    s = s.strip()
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s + "-01T00:00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s + "T00:00:00"
    return s


def parse_ts(s: str) -> datetime.datetime:
    """Parse ISO-8601 (date-only coerced to month/day first-day, §32).

    Raises ValueError on garbage (caller maps to the internal envelope).
    """
    dt = datetime.datetime.fromisoformat(coerce_date(s))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat()


def is_effective(row: dict, at: datetime.datetime) -> bool:
    """status=current AND valid_from<=T AND (valid_to NULL OR >T) (§11)."""
    if row.get("status") != "current":
        return False
    try:
        vf = parse_ts(row["valid_from"])
    except (ValueError, KeyError, TypeError):
        return False
    vt = row.get("valid_to")
    if vt is not None:
        try:
            if parse_ts(vt) <= at:
                return False
        except ValueError:
            return False
    return vf <= at


def started(row: dict, at: datetime.datetime) -> bool:
    """History view (include_superseded): row had started by T.

    The upper bound is dropped — history shows the whole chain behind the
    topic (§11 table: include at now returns B + A flagged).
    """
    try:
        return parse_ts(row["valid_from"]) <= at
    except (ValueError, KeyError, TypeError):
        return False


def in_window(row: dict, at: datetime.datetime) -> bool:
    """Validity window ignoring status (past-window reads with include flag)."""
    try:
        vf = parse_ts(row["valid_from"])
    except (ValueError, KeyError, TypeError):
        return False
    vt = row.get("valid_to")
    if vt is not None:
        try:
            if parse_ts(vt) <= at:
                return False
        except ValueError:
            return False
    return vf <= at


def chain_walk(conn: sqlite3.Connection, family_id: str) -> list[dict]:
    """Oldest-first rows of one family (forward links, indexed, terminates)."""
    rows = conn.execute(
        "SELECT key, namespace, value, type, status, family_id, superseded_by,"
        " valid_from, valid_to, relation_type, uses, score, created_at,"
        " updated_at FROM memories WHERE family_id=? ORDER BY valid_from",
        (family_id,)).fetchall()
    cols = ("key", "namespace", "value", "type", "status", "family_id",
            "superseded_by", "valid_from", "valid_to", "relation_type",
            "uses", "score", "created_at", "updated_at")
    return [dict(zip(cols, r)) for r in rows]


def display_line(row: dict, prev_value: str | None = None) -> str:
    """One-line injection format (§14): current + since + predecessor."""
    since = str(row.get("valid_from", ""))[:10]
    if row.get("status") == "current":
        tail = f"(current, since {since}; previously {prev_value[:60]})" if prev_value \
            else f"(current, since {since})"
    else:
        tail = f"(superseded, was valid since {since})"
    return f"- [{row.get('type')}] {row.get('value')} {tail}"
