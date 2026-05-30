"""FEAT-writer-lowfreq-redesign Step 3 — artist hero + by-spotify + top-tracks.

Pure-unit coverage of the 3 new endpoints' service + router wiring. Real-SQL
ordering for top-tracks lives in tests/integration/test_artist_top_tracks.py
([[feedback-sa-session-lifecycle-mock-blind]]) — mocks here cover routing,
404 paths, and the hero shape; they cannot prove the ORDER BY clause works.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")


def _fake_artist(id_="00000000-0000-0000-0000-000000000001",
                 spotify_id="sp-A1",
                 name="Phoebe Bridgers",
                 photo_url="https://i.example/p.jpg",
                 genres=("indie", "folk"),
                 followers=2_500_000,
                 popularity=72,
                 spotify_url="https://open.spotify.com/artist/sp-A1"):
    a = MagicMock()
    a.id = id_
    a.spotify_id = spotify_id
    a.name = name
    a.photo_url = photo_url
    a.genres = list(genres)
    a.followers = followers
    a.popularity = popularity
    a.spotify_url = spotify_url
    return a


def _client():
    from fastapi.testclient import TestClient

    from app.api.routers import artists as artists_router
    from app.core.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: MagicMock()
    client = TestClient(app)
    return client, artists_router


class TestArtistHeroById:
    def test_returns_hero_when_artist_exists(self, monkeypatch):
        client, _ = _client()
        from app.api.routers import artists as artists_router

        a = _fake_artist()
        svc = MagicMock()
        svc.get_hero_by_id.return_value = type(
            "Hero", (), {}
        )()  # placeholder

        # Patch the service factory in the router so we can drive return values.
        def fake_service(db):
            real = MagicMock()
            real.get_hero_by_id.return_value = _hero(a, album_count=12, track_count=83)
            return real

        monkeypatch.setattr(artists_router, "_service", fake_service)

        r = client.get(f"/api/music/artists/{a.id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == str(a.id)
        assert body["name"] == "Phoebe Bridgers"
        assert body["spotify_id"] == "sp-A1"
        assert body["photo_url"] == "https://i.example/p.jpg"
        assert body["genres"] == ["indie", "folk"]
        assert body["followers"] == 2_500_000
        assert body["popularity"] == 72
        assert body["spotify_url"] == "https://open.spotify.com/artist/sp-A1"
        assert body["album_count"] == 12
        assert body["track_count"] == 83
        assert body["status"] == "ready"

    def test_returns_404_when_missing(self, monkeypatch):
        client, artists_router = _client()

        def fake_service(db):
            real = MagicMock()
            real.get_hero_by_id.return_value = None
            return real

        monkeypatch.setattr(artists_router, "_service", fake_service)

        r = client.get("/api/music/artists/00000000-0000-0000-0000-000000000099")
        assert r.status_code == 404


class TestArtistHeroBySpotify:
    def test_returns_hero_when_row_exists(self, monkeypatch):
        client, artists_router = _client()
        a = _fake_artist(spotify_id="sp-ready")

        def fake_service(db):
            real = MagicMock()
            real.get_hero_by_spotify_id.return_value = _hero(a)
            return real

        monkeypatch.setattr(artists_router, "_service", fake_service)

        r = client.get("/api/music/artists/by-spotify/sp-ready")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["spotify_id"] == "sp-ready"
        assert body["status"] == "ready"

    def test_returns_404_when_no_row(self, monkeypatch):
        """No absorb-tracking table today, so 404 is the only non-ready path
        the endpoint can produce. Frontend polls 404 until ready.
        """
        client, artists_router = _client()

        def fake_service(db):
            real = MagicMock()
            real.get_hero_by_spotify_id.return_value = None
            return real

        monkeypatch.setattr(artists_router, "_service", fake_service)

        r = client.get("/api/music/artists/by-spotify/sp-unknown")
        assert r.status_code == 404

    def test_route_does_not_collide_with_artist_id_path(self, monkeypatch):
        """`by-spotify/{sid}` must be declared before `/{artist_id}` so the
        literal segment wins. If it doesn't, the request would land on
        get_artist with artist_id='by-spotify' and 404 with the wrong reason.
        """
        client, artists_router = _client()
        captured = {}

        def fake_service(db):
            real = MagicMock()

            def _by_spotify(sid):
                captured["called"] = "by_spotify"
                captured["sid"] = sid
                return _hero(_fake_artist(spotify_id=sid))

            def _by_id(aid):
                captured["called"] = "by_id"
                captured["aid"] = aid
                return None

            real.get_hero_by_spotify_id.side_effect = _by_spotify
            real.get_hero_by_id.side_effect = _by_id
            return real

        monkeypatch.setattr(artists_router, "_service", fake_service)
        r = client.get("/api/music/artists/by-spotify/sp-X")
        assert r.status_code == 200
        assert captured["called"] == "by_spotify"
        assert captured["sid"] == "sp-X"


class TestArtistTopTracksRoute:
    def test_returns_list_in_provided_order(self, monkeypatch):
        client, artists_router = _client()
        from app.domain.schemas import TrackItem

        items = [
            TrackItem(
                id="t1", title="Kyoto", track_no=2, duration_sec=187,
                spotify_id="sp-t1",
                album_id="al1", album_title="Punisher",
                cover_url=None, release_date="2020-06-18",
                album_spotify_id="sp-al1",
                artist_name="Phoebe Bridgers", feat_artist_names=[],
            ),
            TrackItem(
                id="t2", title="Motion Sickness", track_no=2, duration_sec=240,
                spotify_id="sp-t2",
                album_id="al2", album_title="Stranger in the Alps",
                cover_url=None, release_date="2017-09-22",
                album_spotify_id="sp-al2",
                artist_name="Phoebe Bridgers", feat_artist_names=[],
            ),
        ]

        def fake_service(db):
            real = MagicMock()
            real.list_top_tracks.return_value = items
            return real

        monkeypatch.setattr(artists_router, "_service", fake_service)

        r = client.get("/api/music/artists/00000000-0000-0000-0000-000000000001/top-tracks")
        assert r.status_code == 200, r.text
        body = r.json()
        assert [t["id"] for t in body] == ["t1", "t2"]
        assert body[0]["album_title"] == "Punisher"

    def test_limit_param_bounds(self, monkeypatch):
        client, artists_router = _client()

        called = {}

        def fake_service(db):
            real = MagicMock()

            def _top(*, artist_id, limit):
                called["limit"] = limit
                return []
            real.list_top_tracks.side_effect = _top
            return real

        monkeypatch.setattr(artists_router, "_service", fake_service)

        r = client.get(
            "/api/music/artists/00000000-0000-0000-0000-000000000001/top-tracks?limit=5"
        )
        assert r.status_code == 200
        assert called["limit"] == 5

        r = client.get(
            "/api/music/artists/00000000-0000-0000-0000-000000000001/top-tracks?limit=0"
        )
        assert r.status_code == 422  # below minimum

        r = client.get(
            "/api/music/artists/00000000-0000-0000-0000-000000000001/top-tracks?limit=51"
        )
        assert r.status_code == 422  # above maximum


def _hero(a, album_count=0, track_count=0):
    from app.domain.schemas import ArtistHero
    return ArtistHero(
        id=str(a.id),
        name=a.name,
        spotify_id=a.spotify_id,
        photo_url=a.photo_url,
        genres=list(a.genres or []),
        followers=a.followers,
        popularity=a.popularity,
        spotify_url=a.spotify_url,
        album_count=album_count,
        track_count=track_count,
        status="ready",
    )
