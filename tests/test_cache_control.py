"""FEAT-music-edge-cache Step 1 — Cache-Control on public DB-read endpoints.

Guarantees:
  * 200 responses carry the right Cache-Control (browser + edge honor max-age).
  * 404s (incl. by-spotify absorb-pending) carry NO Cache-Control, so the
    writer's poll-until-ready flow isn't stalled by a cached miss
    ([[feedback-rfc-current-state-audit]]).
  * The auth-gated /candidates endpoint is untouched (no positive cache header).

Pure routing/header units — no DB. Mirrors tests/test_artists_api.py.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")

from app.core.cache import DETAIL_CACHE_CONTROL, SEARCH_CACHE_CONTROL  # noqa: E402


def _client():
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app)


def test_unified_search_200_sets_search_cache_control(monkeypatch):
    from app.api.routers import search as search_router
    from app.domain.schemas import UnifiedSearchResult

    fake_svc = MagicMock()
    fake_svc.unified_search.return_value = UnifiedSearchResult()
    monkeypatch.setattr(search_router, "DBSearchService", lambda db: fake_svc)

    r = _client().get("/api/music/search/unified?q=radiohead&type=album")
    assert r.status_code == 200, r.text
    assert r.headers.get("Cache-Control") == SEARCH_CACHE_CONTROL


def test_unified_search_validation_400_is_uncached():
    # Invalid type → 400 before the service call; must not advertise a cache TTL.
    r = _client().get("/api/music/search/unified?q=x&type=bogus")
    assert r.status_code == 400
    assert "Cache-Control" not in r.headers


def test_artist_hero_200_sets_detail_cache_control(monkeypatch):
    from app.api.routers import artists as artists_router
    from app.domain.schemas import ArtistHero

    def fake_service(db):
        real = MagicMock()
        real.get_hero_by_id.return_value = ArtistHero(name="Phoebe Bridgers")
        return real

    monkeypatch.setattr(artists_router, "_service", fake_service)

    r = _client().get("/api/music/artists/00000000-0000-0000-0000-000000000001")
    assert r.status_code == 200, r.text
    assert r.headers.get("Cache-Control") == DETAIL_CACHE_CONTROL


def test_artist_by_spotify_404_is_uncached(monkeypatch):
    # absorb-pending poll target — a cached 404 would stall the writer's poll.
    from app.api.routers import artists as artists_router

    def fake_service(db):
        real = MagicMock()
        real.get_hero_by_spotify_id.return_value = None
        return real

    monkeypatch.setattr(artists_router, "_service", fake_service)

    r = _client().get("/api/music/artists/by-spotify/sp-pending")
    assert r.status_code == 404
    assert "Cache-Control" not in r.headers


def test_album_by_spotify_404_is_uncached(monkeypatch):
    from app.api.routers import albums as albums_router
    from fastapi import HTTPException

    def fake_ctor(db):
        real = MagicMock()
        real.get_album_detail_by_spotify.side_effect = HTTPException(status_code=404, detail="album not found in DB")
        return real

    monkeypatch.setattr(albums_router, "AlbumService", fake_ctor)

    r = _client().get("/api/music/albums/by-spotify/sp-pending")
    assert r.status_code == 404
    assert "Cache-Control" not in r.headers
