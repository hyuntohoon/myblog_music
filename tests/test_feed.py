"""FEAT-release-calendar Track A Step 1 — GET /api/music/feed/new-releases.

Service units run over a stubbed repo (the SQL WHERE itself is exercised by
prod smoke — no local DB, [[feedback-local-db-smoke-fallback]]); the service
re-applies the same window/type predicates over the capped fetch, which is the
seam these tests exercise. Router tests mirror tests/test_cache_control.py
(TestClient + mocked service — param caps, Cache-Control on 200 only).
"""
from __future__ import annotations

import os
import uuid
from datetime import date
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")

TODAY = date(2026, 7, 11)


def _client():
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app)


class TestNewReleasesService:
    def _artist(self, name, popularity=50):
        ar = MagicMock()
        ar.id = uuid.uuid4()
        ar.name = name
        ar.popularity = popularity
        return ar

    def _album(self, *, title, release, popularity=50, artists=None,
               album_type="album", spotify_id=None, cover=None):
        al = MagicMock()
        al.id = uuid.uuid4()
        al.title = title
        al.release_date = release
        al.album_type = album_type
        al.cover_url = cover
        al.spotify_id = spotify_id or f"sp_{title}_{release}"
        al.popularity = popularity
        al.artists = artists or []
        return al

    def _svc(self, albums, reviewed_ids=None):
        from app.services.feed_service import FeedService

        svc = FeedService(MagicMock())
        svc.albums = MagicMock()
        svc.albums.list_new_releases.return_value = albums
        svc.albums.reviewed_artist_ids.return_value = reviewed_ids or set()
        return svc

    def test_window_boundary_29_30_31_days(self):
        ar = self._artist("A")
        d29 = self._album(title="T29", release=date(2026, 6, 12), artists=[ar])
        d30 = self._album(title="T30", release=date(2026, 6, 11), artists=[ar])
        d31 = self._album(title="T31", release=date(2026, 6, 10), artists=[ar])
        svc = self._svc([d29, d30, d31])
        res = svc.get_new_releases(days=30, limit=12, today=TODAY)
        # since = today - 30d = 2026-06-11 inclusive; the 31-day row is out.
        assert sorted(i.title for i in res.items) == ["T29", "T30"]
        svc.albums.list_new_releases.assert_called_once()
        assert svc.albums.list_new_releases.call_args.kwargs["since"] == date(2026, 6, 11)

    def test_album_type_filter_excludes_singles(self):
        ar = self._artist("A")
        album = self._album(title="Full", release=date(2026, 7, 1), artists=[ar])
        single = self._album(title="One-off", release=date(2026, 7, 1),
                             artists=[ar], album_type="single")
        res = self._svc([album, single]).get_new_releases(days=30, limit=12, today=TODAY)
        assert [i.title for i in res.items] == ["Full"]

    def test_dedup_keeps_highest_popularity_then_latest_date(self):
        ar = self._artist("Band")
        low = self._album(title="Repeat", release=date(2026, 6, 20), popularity=20,
                          artists=[ar], spotify_id="low")
        high = self._album(title="repeat ", release=date(2026, 6, 18), popularity=80,
                           artists=[ar], spotify_id="high")
        res = self._svc([low, high]).get_new_releases(days=30, limit=12, today=TODAY)
        assert len(res.items) == 1
        assert res.items[0].spotify_album_id == "high"

        # popularity tie → latest release_date wins (RFC dedup tiebreak).
        early = self._album(title="Tie", release=date(2026, 6, 18), popularity=50,
                            artists=[ar], spotify_id="early")
        late = self._album(title="tie", release=date(2026, 6, 20), popularity=50,
                           artists=[ar], spotify_id="late")
        res = self._svc([early, late]).get_new_releases(days=30, limit=12, today=TODAY)
        assert res.items[0].spotify_album_id == "late"

    def test_ranking_artist_popularity_then_release_date(self):
        pop95 = self._artist("Star", popularity=95)
        pop40 = self._artist("Indie", popularity=40)
        older_star = self._album(title="StarOld", release=date(2026, 6, 15), artists=[pop95])
        newer_star = self._album(title="StarNew", release=date(2026, 7, 1), artists=[pop95])
        indie = self._album(title="IndieNew", release=date(2026, 7, 10), artists=[pop40])
        res = self._svc([indie, older_star, newer_star]).get_new_releases(
            days=30, limit=12, today=TODAY
        )
        assert [i.title for i in res.items] == ["StarNew", "StarOld", "IndieNew"]
        assert res.items[0].artist_popularity == 95

    def test_reviewed_artist_flag_on_off(self):
        reviewed_ar = self._artist("Reviewed", popularity=90)
        fresh_ar = self._artist("Fresh", popularity=80)
        a1 = self._album(title="A1", release=date(2026, 7, 1), artists=[reviewed_ar])
        a2 = self._album(title="A2", release=date(2026, 7, 1), artists=[fresh_ar])
        svc = self._svc([a1, a2], reviewed_ids={reviewed_ar.id})
        res = svc.get_new_releases(days=30, limit=12, today=TODAY)
        by_title = {i.title: i for i in res.items}
        assert by_title["A1"].reviewed_artist is True
        assert by_title["A2"].reviewed_artist is False
        # the reviewed lookup got exactly the credited-artist id set
        assert svc.albums.reviewed_artist_ids.call_args.args[0] == {
            reviewed_ar.id, fresh_ar.id
        }

    def test_limit_applied_after_dedup(self):
        ar = self._artist("A")
        albums = [
            self._album(title=f"T{i}", release=date(2026, 7, 1), artists=[ar])
            for i in range(20)
        ]
        res = self._svc(albums).get_new_releases(days=30, limit=5, today=TODAY)
        assert len(res.items) == 5 and res.total == 5

    def test_empty_window(self):
        res = self._svc([]).get_new_releases(days=30, limit=12, today=TODAY)
        assert res.items == [] and res.total == 0 and res.window_days == 30


class TestNewReleasesRouter:
    def test_200_sets_search_cache_control(self, monkeypatch):
        from app.api.routers import feed as feed_router
        from app.core.cache import SEARCH_CACHE_CONTROL
        from app.domain.schemas import NewReleasesResult

        fake = MagicMock()
        fake.get_new_releases.return_value = NewReleasesResult(window_days=30, total=0)
        monkeypatch.setattr(feed_router, "FeedService", lambda db: fake)

        r = _client().get("/api/music/feed/new-releases?days=30&limit=12")
        assert r.status_code == 200, r.text
        assert r.headers.get("Cache-Control") == SEARCH_CACHE_CONTROL
        assert r.json() == {"items": [], "window_days": 30, "total": 0}

    def test_param_caps_422(self):
        assert _client().get("/api/music/feed/new-releases?days=91").status_code == 422
        assert _client().get("/api/music/feed/new-releases?limit=51").status_code == 422
        assert _client().get("/api/music/feed/new-releases?days=0").status_code == 422

    def test_validation_422_is_uncached(self):
        r = _client().get("/api/music/feed/new-releases?days=91")
        assert "Cache-Control" not in r.headers
