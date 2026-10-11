import numpy as np
import pytest

from memo import embeddings, rank


def test_fuse_matches_worked_example():
    assert rank.fuse(0.9, 1.0, 5, 14.0) == pytest.approx(0.925, abs=1e-3)
    v_only = rank.fuse(0.9, 0.0, 5, 14.0)
    assert v_only == pytest.approx(0.6 * 0.95 + 0.15 * 0.6997, abs=1e-3)


def test_order_tiebreak():
    rows = [
        {"key": "b", "score": 0.5, "uses": 1, "updated_at": "2026-10-02T00:00:00+00:00"},
        {"key": "a", "score": 0.5, "uses": 1, "updated_at": "2026-10-02T00:00:00+00:00"},
        {"key": "c", "score": 0.9, "uses": 0, "updated_at": "2026-10-01T00:00:00+00:00"},
        {"key": "d", "score": 0.5, "uses": 9, "updated_at": "2026-10-01T00:00:00+00:00"},
    ]
    assert [r["key"] for r in rank.order(rows)] == ["c", "d", "a", "b"]


def test_embed_deterministic():
    embeddings.force_fallback = True
    try:
        a, b = embeddings.embed(["prefers tabs", "prefers tabs"])
        assert len(a) == 384 * 4 == 1536
        assert a == b
        assert np.frombuffer(a, dtype=np.float32).shape == (384,)
        assert embeddings.cosine(a, a) == pytest.approx(1.0)
    finally:
        embeddings.force_fallback = False


def test_offline_failure(monkeypatch):
    monkeypatch.setattr(embeddings, "load_model", lambda: (_ for _ in ()).throw(
        RuntimeError("fetch failed: offline")))
    with pytest.raises(RuntimeError, match="embedding model unavailable offline"):
        embeddings.embed(["hello"])
