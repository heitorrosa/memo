from __future__ import annotations

import sys

from memo import errors

VERSION = 3


def startup_line(db_path: str, has_vec: bool) -> str:
    return (f"memo v3 db={db_path} vec={'vec' if has_vec else 'bruteforce'}"
            f" memory=on skills=on")


def log_operation(operation: str, namespace: str, elapsed_ms: float, row_count: int, action: str) -> None:
    print(f"op={operation} ns={namespace} ms={elapsed_ms:.1f} rows={row_count} action={action}",
          file=sys.stderr)


def internal_error(exc: Exception) -> dict:
    first = str(exc).splitlines()[0] if str(exc) else ""
    detail = f"{type(exc).__name__}: {first}".rstrip(": ")
    return errors.err("internal", detail)
