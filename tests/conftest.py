"""Shared test fixtures.

FEAT-music-edge-cache Step 5 added a per-process TTL cache in front of
SearchService.unified_search. That cache is module-level, so without isolation it
would leak across tests — e.g. several unit tests call
`unified_search(q="MatchedAlbum", limit=20, offset=0)` against *different* mocked
DBs and must each see their own result, not the first one cached. Clear it before
every test. Lazy import keeps config off the collection path
([[reference-backend-test-config-import-collection]]).
"""
import pytest


@pytest.fixture(autouse=True)
def _clear_unified_search_cache():
    from app.services.search_service import _unified_cache
    _unified_cache.clear()
    yield
    _unified_cache.clear()
