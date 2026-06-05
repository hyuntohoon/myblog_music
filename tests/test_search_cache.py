"""FEAT-music-edge-cache Step 5 — per-process TTL cache for unified_search.

Proves the cache fronts the heavy compute: identical args compute once, distinct
args recompute, and `types=None` collapses to the same key as the full set. The
autouse fixture in conftest.py clears the module cache between tests.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")


def _service_with_counting_compute():
    from app.domain.schemas import UnifiedSearchResult
    from app.services.search_service import SearchService

    svc = SearchService(MagicMock())
    calls = {"n": 0}

    def fake_compute(**kwargs):
        calls["n"] += 1
        return UnifiedSearchResult()

    svc._compute_unified_search = fake_compute  # type: ignore[method-assign]
    return svc, calls


def test_identical_calls_compute_once():
    svc, calls = _service_with_counting_compute()
    a = svc.unified_search(q="radiohead", limit=20, offset=0)
    b = svc.unified_search(q="radiohead", limit=20, offset=0)
    assert calls["n"] == 1, "second identical call must hit the cache"
    assert a is b, "cache returns the same object"


def test_distinct_args_recompute():
    svc, calls = _service_with_counting_compute()
    svc.unified_search(q="radiohead", limit=20, offset=0)
    svc.unified_search(q="radiohead", limit=20, offset=20)  # different offset
    svc.unified_search(q="aphex", limit=20, offset=0)       # different q
    assert calls["n"] == 3


def test_types_none_collapses_to_full_set_key():
    svc, calls = _service_with_counting_compute()
    svc.unified_search(q="radiohead", limit=20, offset=0, types=None)
    svc.unified_search(q="radiohead", limit=20, offset=0, types={"album", "artist", "track"})
    assert calls["n"] == 1, "types=None and the full set must share one cache entry"


def test_explain_is_a_separate_entry():
    svc, calls = _service_with_counting_compute()
    svc.unified_search(q="radiohead", limit=20, offset=0)
    svc.unified_search(q="radiohead", limit=20, offset=0, explain=True)
    assert calls["n"] == 2, "explain=True must not reuse the non-explain entry"
