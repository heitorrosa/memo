"""Skills domain: put/get/update/install + sync dual-mode + L0 index (spec §§18-21).

Import rule (§37): this module imports ONLY db/errors (never memory.py).
The only cross-domain readers are summary.py (L0 join) and maintenance.py
(tombstone expiry) — both read-only/bounded callers, never imports here.
"""
