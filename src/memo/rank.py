"""Fusion rank (spec §12, M-001/M-002/M-014)."""

from __future__ import annotations

import math

WEIGHT_VECTOR, WEIGHT_TEXT, WEIGHT_RETENTION = 0.6, 0.25, 0.15


def retention(age_days: float, stability: float) -> float:
    return math.exp(-age_days / stability)


def fuse(vector: float, text: float, age_days: float, stability: float) -> float:
    """Rank = 0.6*V + 0.25*T + 0.15*R. vector arg is raw cosine, remapped (cos+1)/2."""
    return WEIGHT_VECTOR * (vector + 1.0) / 2.0 + WEIGHT_TEXT * text + WEIGHT_RETENTION * retention(age_days, stability)


def order(rows: list[dict]) -> list[dict]:
    """Deterministic: score desc, uses desc, updated_at desc, key asc (M-014)."""
    rows = sorted(rows, key=lambda row: row["key"])
    rows = sorted(rows, key=lambda row: str(row.get("updated_at", "")), reverse=True)
    rows = sorted(rows, key=lambda row: row.get("uses", 0), reverse=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)
