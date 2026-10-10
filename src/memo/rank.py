"""Fusion rank (spec §12, M-001/M-002/M-014)."""

from __future__ import annotations

import math

W_V, W_T, W_R = 0.6, 0.25, 0.15


def retention(age_days: float, stability: float) -> float:
    return math.exp(-age_days / stability)


def fuse(V: float, T: float, age_days: float, stability: float) -> float:
    """Rank = 0.6*V + 0.25*T + 0.15*R. V arg is raw cosine, remapped (cos+1)/2."""
    return W_V * (V + 1.0) / 2.0 + W_T * T + W_R * retention(age_days, stability)


def order(rows: list[dict]) -> list[dict]:
    """Deterministic: score desc, uses desc, updated_at desc, key asc (M-014)."""
    rows = sorted(rows, key=lambda r: r["key"])
    rows = sorted(rows, key=lambda r: str(r.get("updated_at", "")), reverse=True)
    rows = sorted(rows, key=lambda r: r.get("uses", 0), reverse=True)
    return sorted(rows, key=lambda r: r["score"], reverse=True)
