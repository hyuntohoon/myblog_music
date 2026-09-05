"""AUDIT-2026-07-26 A-3 (twin) — a malformed catalog id must not 500.

The audit filed A-3 against the backend's /api/research/* routes. The same
defect was live here, on four routes that take no JWT at all. Measured against
prod 2026-08-02, before the fix:

    500  /api/music/albums/not-a-uuid
    500  /api/music/artists/not-a-uuid
    500  /api/music/artists/not-a-uuid/albums
    500  /api/music/artists/not-a-uuid/top-tracks
    404  /api/music/albums/by-spotify/not-a-uuid   <- correct, and must stay

The by-spotify routes are the reason this is a guard at four call sites and not
a global id validator: Spotify ids are not UUIDs, so those routes must keep
accepting arbitrary strings.

The assertion that matters is that the DB is never touched — "did not reach the
driver" is the repair, the status code is only how it looks from outside.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")

BAD = "not-a-uuid"


def _client_and_db():
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.main import app

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), db


@pytest.mark.parametrize(
    "path",
    [
        f"/api/music/albums/{BAD}",
        f"/api/music/artists/{BAD}",
        f"/api/music/artists/{BAD}/albums",
        f"/api/music/artists/{BAD}/top-tracks",
        # FEAT-youtube-playback-provider A2. The first AUTHENTICATED member of
        # this class — the suite runs with ENV=local so the guard is bypassed and
        # the route is reached exactly as the public four are.
        f"/api/music/search/youtube-candidates?track_id={BAD}",
    ],
)
def test_malformed_id_is_404_and_never_queries(path):
    client, db = _client_and_db()

    r = client.get(path)

    assert r.status_code == 404, r.text
    db.execute.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "/api/music/albums/by-spotify/4aawyAB9vmqN3uQ7FjRGTy",
        "/api/music/artists/by-spotify/sp-anything",
    ],
)
def test_by_spotify_still_accepts_non_uuid_ids(path):
    """Guarding these would break the writer's absorb-poll flow outright."""
    client, db = _client_and_db()
    db.execute.return_value.scalars.return_value.first.return_value = None

    r = client.get(path)

    assert r.status_code == 404  # not found, but it DID look
    db.execute.assert_called()


def test_uncacheable_404_stays_uncacheable():
    """A guarded 404 must not advertise a TTL — the by-spotify poll relies on
    404s being uncached, and a cached 404 on a real id would freeze a page."""
    client, _ = _client_and_db()

    r = client.get(f"/api/music/albums/{BAD}")

    assert r.status_code == 404
    assert r.headers.get("Cache-Control") is None
