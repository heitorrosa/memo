from __future__ import annotations

import datetime
import re
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
    dt = datetime.datetime.fromisoformat(coerce_date(timestamp))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat()


def is_effective(row: dict, at: datetime.datetime) -> bool:
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
    valid_until = row.get("valid_until")
    if not valid_until:
        return False
    try:
        return parse_ts(valid_until) <= at
    except ValueError:
        return False


def started(row: dict, at: datetime.datetime) -> bool:
    try:
        return parse_ts(row["valid_from"]) <= at
    except (ValueError, KeyError, TypeError):
        return False


def in_window(row: dict, at: datetime.datetime) -> bool:
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
