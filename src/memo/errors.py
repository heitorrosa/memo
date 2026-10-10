"""Error envelope stub (spec §7). Full code table lands in L4; stub serves T1-2 tests."""

from __future__ import annotations

ENVELOPE_CODES = (
    "empty_query",
    "not_found",
    "trivia_rejected",
    "cap_evicted",
    "hash_mismatch",
    "truncated",
    "internal",
)


def err(code: str, detail: str) -> dict:
    assert code in ENVELOPE_CODES, f"unknown envelope code {code}"
    return {"error": code, "detail": detail}
