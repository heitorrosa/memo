"""Pure tool impls (catch-all -> internal envelope, never raise).

Peewee note: binding lives in the domain call sites (memory.save/search/
remove, skills.*) resolved per-conn via db.orm_db — this layer stays
lock + envelope + delegation only, no SQL of its own.
"""
from __future__ import annotations

import time

from memo.memory.remove import remove
from memo.memory.save import save
from memo.memory.search import search
from memo.server.shared import internal_error, log_operation
from memo.skills.crud import get, put, update
from memo.skills.install import install
from memo.skills.sync import sync


def do_memory_search(conn, query, limit=10, namespace="default",
                     valid_at="now", as_known_at=None,
                     include_superseded=False, lock=None):
    start = time.perf_counter()
    try:
        if lock is not None:
            with lock:
                out = search(conn, query, limit=limit,
                             namespace=namespace, valid_at=valid_at,
                             as_known_at=as_known_at,
                             include_superseded=include_superseded)
        else:
            out = search(conn, query, limit=limit, namespace=namespace,
                         valid_at=valid_at, as_known_at=as_known_at,
                         include_superseded=include_superseded)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)
    row_count = len(out) if isinstance(out, list) else 0
    log_operation("memory_search", namespace, (time.perf_counter() - start) * 1000, row_count,
         out.get("error", "-") if isinstance(out, dict) else "-")
    return out


def do_memory_save(conn, key, value, typ="context", namespace="default",
                   valid_from=None, lock=None):
    start = time.perf_counter()
    try:
        if lock is not None:
            with lock:
                out = save(conn, key, value, type=typ,
                           namespace=namespace, valid_from=valid_from)
        else:
            out = save(conn, key, value, type=typ, namespace=namespace,
                       valid_from=valid_from)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)
    log_operation("memory_save", namespace, (time.perf_counter() - start) * 1000, 1,
         out.get("action", out.get("error", "-")))
    return out


def do_memory_delete(conn, key, namespace="default", lock=None):
    start = time.perf_counter()
    try:
        if lock is not None:
            with lock:
                out = remove(conn, key, namespace=namespace)
        else:
            out = remove(conn, key, namespace=namespace)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)
    log_operation("memory_delete", namespace, (time.perf_counter() - start) * 1000, 1,
         out.get("action", "-"))
    return out


def sync_call_locked(conn, harness, known, filt, offset, limit, lock,
                     capabilities=None):
    if lock is not None:
        with lock:
            return sync(conn, harness, known=known, filter=filt,
                        offset=offset, limit=limit,
                        capabilities=capabilities)
    return sync(conn, harness, known=known, filter=filt,
                offset=offset, limit=limit, capabilities=capabilities)


def do_skill_sync(conn, harness, known=None, filt=None, offset=0, limit=None,
                  lock=None, capabilities=None):
    start = time.perf_counter()
    try:
        out = sync_call_locked(conn, harness, known, filt, offset, limit, lock,
                               capabilities)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)
    row_count = len(out.get("files_to_write", [])) if isinstance(out, dict) else 0
    log_operation("skill_sync", "-", (time.perf_counter() - start) * 1000, row_count,
         out.get("mode", out.get("error", "-")) if isinstance(out, dict)
         else "-")
    return out


def do_skill_get(conn, name, version=None, lock=None):
    try:
        if lock is not None:
            with lock:
                return get(conn, name, version=version)
        return get(conn, name, version=version)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)


def do_skill_put(conn, name, description, body, source="local", lock=None):
    try:
        if lock is not None:
            with lock:
                return put(conn, name, description, body, source=source)
        return put(conn, name, description, body, source=source)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)


def do_skill_update(conn, name, patch, lock=None):
    try:
        if lock is not None:
            with lock:
                return update(conn, name, patch)
        return update(conn, name, patch)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)


def do_skill_install(conn, repository, skill_name, fetch=None, lock=None):
    try:
        fetch_kwargs = {} if fetch is None else {"fetch": fetch}
        if lock is not None:
            with lock:
                return install(conn, repository, skill_name, **fetch_kwargs)
        return install(conn, repository, skill_name, **fetch_kwargs)
    except Exception as exc:  # noqa: BLE001 — boundary: map to envelope
        return internal_error(exc)
