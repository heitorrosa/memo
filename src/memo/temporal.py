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


def token_words(text: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z]+", text)} - STOP_WORDS


def has_overlap(first: str, second: str) -> bool:
    first_words, second_words = token_words(first), token_words(second)
    return bool(first_words & second_words)


def classify(old, new_key: str, new_value: str, judge=None) -> str:
    """Cheap-first 5-way classify. `old` is a row dict or a value string.

    Judge fires ONLY on the narrow negation path (M-009); a non-token
    judge output falls back to extends + stderr log (never crashes save).
    """
    old_value = old.get("value") if isinstance(old, dict) else str(old)
    old_text, new_text = old_value or "", new_value or ""
    if REASSERT_PATTERN.search(new_text):
        return "reasserts"
    if NEGATION_PATTERN.search(new_text) or NEGATION_PATTERN.search(old_text):
        if judge is not None:
            try:
                verdict = str(judge(old_text, new_key, new_text)).strip().lower()
            except Exception:
                verdict = ""
            if verdict in OUTCOMES:
                return verdict
            print(f"judge fallback: non-token output {verdict!r} -> extends",
                  file=sys.stderr)
        return "contradicts"
    if ADDITIVE_PATTERN.search(new_text):
        return "qualifies" if has_overlap(old_text, new_text) else "unrelated"
    return "extends"


def coerce_date(text: str) -> str:
    text = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text + "-01T00:00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text + "T00:00:00"
    return text


def parse_ts(timestamp: str) -> datetime.datetime:
    """Parse ISO-8601 (date-only coerced to month/day first-day, §32).

    Raises ValueError on garbage (caller maps to the internal envelope).
    """
    dt = datetime.datetime.fromisoformat(coerce_date(timestamp))
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
        valid_from_dt = parse_ts(row["valid_from"])
    except (ValueError, KeyError, TypeError):
        return False
    valid_to_dt = row.get("valid_to")
    if valid_to_dt is not None:
        try:
            if parse_ts(valid_to_dt) <= at:
                return False
        except ValueError:
            return False
    return valid_from_dt <= at


def is_expired(row: dict, at: datetime.datetime) -> bool:
    """User-set TTL: valid_until present AND <= T means decayed out of ranking.

    NULL/missing/unparseable valid_until never expires (lenient, like the
    other predicates). Boundary follows is_effective: valid_until == T counts
    as expired, mirroring valid_to <= T ending validity.
    """
    valid_until = row.get("valid_until")
    if not valid_until:
        return False
    try:
        return parse_ts(valid_until) <= at
    except ValueError:
        return False


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
        valid_from_dt = parse_ts(row["valid_from"])
    except (ValueError, KeyError, TypeError):
        return False
    valid_to_dt = row.get("valid_to")
    if valid_to_dt is not None:
        try:
            if parse_ts(valid_to_dt) <= at:
                return False
        except ValueError:
            return False
    return valid_from_dt <= at


def chain_walk(conn: sqlite3.Connection, family_id: str) -> list[dict]:
    """Oldest-first rows of one family (forward links, indexed, terminates)."""
    # raw: temporal stays ORM-free per import rule §37 — predicate-adjacent read.
    rows = conn.execute(
        "SELECT key, namespace, value, type, status, family_id, superseded_by,"
        " valid_from, valid_to, valid_until, agent_id, relation_type, uses, score, created_at,"
        " updated_at FROM memories WHERE family_id=? ORDER BY valid_from",
        (family_id,)).fetchall()
    columns = ("key", "namespace", "value", "type", "status", "family_id",
               "superseded_by", "valid_from", "valid_to", "valid_until", "agent_id", "relation_type",
               "uses", "score", "created_at", "updated_at")
    return [dict(zip(columns, record)) for record in rows]


def display_line(row: dict, prev_value: str | None = None) -> str:
    """One-line injection format (§14): current + since + predecessor."""
    since = str(row.get("valid_from", ""))[:10]
    if row.get("status") == "current":
        tail = f"(current, since {since}; previously {prev_value[:60]})" if prev_value \
            else f"(current, since {since})"
    else:
        tail = f"(superseded, was valid since {since})"
    return f"- [{row.get('type')}] {row.get('value')} {tail}"
