"""Skills domain: put/get/update/install + sync dual-mode + L0 index (spec §§18-21).

Import rule (§37): this module imports ONLY db/errors (never memory.py).
The only cross-domain readers are summary.py (L0 join) and maintenance.py
(tombstone expiry) — both read-only/bounded callers, never imports here.
"""
from memo.skills.crud import (
    build_frontmatter,
    get,
    hash_body,
    l0_index,
    l0_line,
    put,
    shorten_text,
    split_revisions,
    tombstone,
    update,
    utc_now,
)
from memo.skills.install import default_fetch, install, split_frontmatter
from memo.skills.sync import (
    ORIENTATION,
    PAGE_BYTES,
    PAGE_SIZE,
    WEB_PAGE,
    WEB_TRUNCATE_CHARS,
    WEB_TRUNCATE_SKILLS,
    live_rows,
    rank_filter,
    sync,
    sync_files,
    sync_web,
)

__all__ = [
    "ORIENTATION",
    "PAGE_BYTES",
    "PAGE_SIZE",
    "WEB_PAGE",
    "WEB_TRUNCATE_CHARS",
    "WEB_TRUNCATE_SKILLS",
    "build_frontmatter",
    "default_fetch",
    "get",
    "hash_body",
    "install",
    "l0_index",
    "l0_line",
    "live_rows",
    "put",
    "rank_filter",
    "shorten_text",
    "split_frontmatter",
    "split_revisions",
    "sync",
    "sync_files",
    "sync_web",
    "tombstone",
    "update",
    "utc_now",
]
