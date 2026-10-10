"""Shared server primitives: domain flags, logging, error envelope (spec §§4,7)."""
from __future__ import annotations

import os
import sys

from memo import errors

VERSION = 3


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def resolve_domains() -> tuple[bool, bool]:
    """(memory_on, skills_on) from MEMO_DISABLE_* (spec §4.1b)."""
    return not env_flag("MEMO_DISABLE_MEMORY"), not env_flag("MEMO_DISABLE_SKILLS")


def startup_line(db_path: str, has_vec: bool, memory_on: bool,
                 skills_on: bool) -> str:
    return (f"memo v3 db={db_path} vec={'vec' if has_vec else 'bruteforce'}"
            f" memory={'on' if memory_on else 'off'}"
            f" skills={'on' if skills_on else 'off'}")


def log_operation(operation: str, namespace: str, elapsed_ms: float, row_count: int, action: str) -> None:
    # NEVER values/bodies/keys/bundle text (keys can be PII-shaped).
    print(f"op={operation} ns={namespace} ms={elapsed_ms:.1f} rows={row_count} action={action}",
          file=sys.stderr)


def internal_error(exc: Exception) -> dict:
    first = str(exc).splitlines()[0] if str(exc) else ""
    detail = f"{type(exc).__name__}: {first}".rstrip(": ")
    return errors.err("internal", detail)
