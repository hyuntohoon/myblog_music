"""Unit tests — no external services required."""
import uuid
import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")


class TestCognitoAuthBypass:
    """require_cognito_token must bypass when ENV is local/dev or pool ID is unset."""

    def test_bypasses_when_env_local(self, monkeypatch):
        from app.core import auth, config
        monkeypatch.setattr(config.settings, "ENV", "local")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "some-pool")
        result = auth.require_cognito_token(credentials=None)
        assert result == {}

    def test_bypasses_when_env_dev(self, monkeypatch):
        from app.core import auth, config
        monkeypatch.setattr(config.settings, "ENV", "dev")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "some-pool")
        result = auth.require_cognito_token(credentials=None)
        assert result == {}

    def test_bypasses_when_pool_id_empty(self, monkeypatch):
        from app.core import auth, config
        monkeypatch.setattr(config.settings, "ENV", "prod")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "")
        result = auth.require_cognito_token(credentials=None)
        assert result == {}

    def test_raises_401_when_no_token_in_prod(self, monkeypatch):
        from fastapi import HTTPException
        from app.core import auth, config
        monkeypatch.setattr(config.settings, "ENV", "prod")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "ap-northeast-2_abc123")
        with pytest.raises(HTTPException) as exc_info:
            auth.require_cognito_token(credentials=None)
        assert exc_info.value.status_code == 401

    def test_raises_401_on_invalid_jwt_in_prod(self, monkeypatch):
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        from app.core import auth, config
        monkeypatch.setattr(config.settings, "ENV", "prod")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "ap-northeast-2_abc123")
        monkeypatch.setattr(auth, "_get_jwks", lambda: {"keys": []})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.valid.jwt")
        with pytest.raises(HTTPException) as exc_info:
            auth.require_cognito_token(credentials=creds)
        assert exc_info.value.status_code == 401


class TestAlbumServiceExternalUrl:
    """AlbumOut.external_url must be populated from Album.ext_refs['spotify_url']."""

    def _make_album(self, ext_refs: dict):
        from datetime import date
        al = MagicMock()
        al.id = uuid.uuid4()
        al.title = "OK Computer"
        al.release_date = date(1997, 6, 16)
        al.cover_url = "https://example.com/cover.jpg"
        al.album_type = "album"
        al.spotify_id = "test_spotify_id"
        al.ext_refs = ext_refs
        return al

    def test_external_url_populated_from_ext_refs(self):
        from app.services.album_service import AlbumService
        from app.domain.schemas import AlbumDetail

        al = self._make_album({"spotify_url": "https://open.spotify.com/album/abc123"})
        mock_db = MagicMock()
        svc = AlbumService(mock_db)
        svc.albums = MagicMock()
        svc.artists = MagicMock()
        svc.tracks = MagicMock()
        svc.albums.get_with_artists.return_value = (al, [])
        svc.tracks.get_by_album.return_value = []

        result: AlbumDetail = svc.get_album_detail(str(al.id))

        assert result.album.external_url == "https://open.spotify.com/album/abc123"

    def test_external_url_none_when_ext_refs_empty(self):
        from app.services.album_service import AlbumService

        al = self._make_album({})
        mock_db = MagicMock()
        svc = AlbumService(mock_db)
        svc.albums = MagicMock()
        svc.artists = MagicMock()
        svc.tracks = MagicMock()
        svc.albums.get_with_artists.return_value = (al, [])
        svc.tracks.get_by_album.return_value = []

        result = svc.get_album_detail(str(al.id))

        assert result.album.external_url is None

    def test_external_url_none_when_ext_refs_null(self):
        from app.services.album_service import AlbumService

        al = self._make_album(None)
        mock_db = MagicMock()
        svc = AlbumService(mock_db)
        svc.albums = MagicMock()
        svc.artists = MagicMock()
        svc.tracks = MagicMock()
        svc.albums.get_with_artists.return_value = (al, [])
        svc.tracks.get_by_album.return_value = []

        result = svc.get_album_detail(str(al.id))

        assert result.album.external_url is None
