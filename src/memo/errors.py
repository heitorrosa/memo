from __future__ import annotations

ENVELOPE_CODES = (
    "empty_query",
    "invalid_input",
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
