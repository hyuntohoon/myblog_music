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

    def _make_album(self, ext_refs):
        from datetime import date
        al = MagicMock()
        al.id = uuid.uuid4()
        al.title = "OK Computer"
        al.release_date = date(1997, 6, 16)
        al.cover_url = "https://example.com/cover.jpg"
        al.album_type = "album"
        al.spotify_id = "test_spotify_id"
        al.ext_refs = ext_refs
        al.label = None
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


class TestAlbumServiceWriteUxBundle:
    """FEAT-write-ux-bundle PR-2: AlbumOut.label + TrackOut.feat_artist_names."""

    def _make_album(self, *, label=None, ext_refs=None):
        from datetime import date
        al = MagicMock()
        al.id = uuid.uuid4()
        al.title = "Album"
        al.release_date = date(2020, 1, 1)
        al.cover_url = None
        al.album_type = "album"
        al.spotify_id = "alb_sp"
        al.ext_refs = ext_refs or {}
        al.label = label
        return al

    def _make_artist(self, name: str):
        ar = MagicMock()
        ar.id = uuid.uuid4()
        ar.name = name
        ar.spotify_id = f"sp_{name}"
        return ar

    def _make_track(self, *, title: str, track_no: int, artists: list):
        t = MagicMock()
        t.id = uuid.uuid4()
        t.title = title
        t.track_no = track_no
        t.duration_sec = 200
        t.spotify_id = f"sp_trk_{track_no}"
        t.artists = artists
        return t

    def _svc(self, *, album, album_artists, tracks):
        from app.services.album_service import AlbumService
        mock_db = MagicMock()
        svc = AlbumService(mock_db)
        svc.albums = MagicMock()
        svc.artists = MagicMock()
        svc.tracks = MagicMock()
        svc.albums.get_with_artists.return_value = (album, album_artists)
        svc.tracks.get_by_album.return_value = tracks
        return svc

    def test_album_label_populated(self):
        al = self._make_album(label="Parlophone")
        svc = self._svc(album=al, album_artists=[], tracks=[])
        result = svc.get_album_detail(str(al.id))
        assert result.album.label == "Parlophone"

    def test_album_label_none(self):
        al = self._make_album(label=None)
        svc = self._svc(album=al, album_artists=[], tracks=[])
        result = svc.get_album_detail(str(al.id))
        assert result.album.label is None

    def test_feat_excludes_album_primary_artists(self):
        primary = self._make_artist("Primary")
        guest = self._make_artist("Guest")
        al = self._make_album()
        t = self._make_track(title="Song", track_no=1, artists=[primary, guest])
        svc = self._svc(album=al, album_artists=[primary], tracks=[t])
        result = svc.get_album_detail(str(al.id))
        assert result.tracks[0].feat_artist_names == ["Guest"]

    def test_feat_sorted_and_dedupes_album_artists_only(self):
        a1 = self._make_artist("Alpha")
        a2 = self._make_artist("Bravo")
        zulu = self._make_artist("Zulu")
        delta = self._make_artist("Delta")
        al = self._make_album()
        t = self._make_track(title="Song", track_no=1, artists=[zulu, delta, a1, a2])
        svc = self._svc(album=al, album_artists=[a1, a2], tracks=[t])
        result = svc.get_album_detail(str(al.id))
        assert result.tracks[0].feat_artist_names == ["Delta", "Zulu"]

    def test_feat_empty_when_no_guest(self):
        primary = self._make_artist("Primary")
        al = self._make_album()
        t = self._make_track(title="Song", track_no=1, artists=[primary])
        svc = self._svc(album=al, album_artists=[primary], tracks=[t])
        result = svc.get_album_detail(str(al.id))
        assert result.tracks[0].feat_artist_names == []


class TestCandidateSearchResultSchema:
    """PR-12: /api/music/search/candidates response_model — front consumes typed shape."""

    def test_parses_full_service_output(self):
        from app.domain.schemas import CandidateSearchResult

        # Mirrors what CandidateSearchService.search_candidates returns when
        # all 3 types are requested + a non-empty Spotify response.
        raw = {
            "albums": [{
                "spotify_id": "alb_111",
                "title": "Mock Album",
                "album_type": "album",
                "release_date": "2022-01-01",
                "cover_url": "http://img",
                "artist_name": "Mock Artist",
                "artist_spotify_id": "art_1",
                "external_url": "http://sp/alb_111",
            }],
            "albums_pagination": {"total": 1, "limit": 10, "offset": 0,
                                  "next": None, "previous": None, "href": None},
            "artists": [{
                "spotify_id": "art_1",
                "name": "Mock Artist",
                "genres": [],
                "photo_url": None,
                "external_url": "http://sp/art_1",
            }],
            "artists_pagination": {"total": 1, "limit": 10, "offset": 0,
                                   "next": None, "previous": None, "href": None},
            "tracks": [{
                "spotify_id": "trk_1",
                "title": "Song A",
                "duration_ms": 100000,
                "track_number": 1,
                "album": {
                    "spotify_id": "alb_111",
                    "title": "Mock Album",
                    "release_date": "2022-01-01",
                    "cover_url": "http://img",
                },
                "artist_name": "Mock Artist",
                "artist_spotify_id": "art_1",
                "external_url": "http://sp/trk_1",
            }],
            "tracks_pagination": {"total": 1, "limit": 10, "offset": 0,
                                  "next": None, "previous": None, "href": None},
        }

        result = CandidateSearchResult.model_validate(raw)

        assert result.albums and result.albums[0].spotify_id == "alb_111"
        assert result.albums[0].artist_spotify_id == "art_1"
        assert result.artists and result.artists[0].name == "Mock Artist"
        assert result.tracks and result.tracks[0].album is not None
        assert result.tracks[0].album.spotify_id == "alb_111"
        assert result.albums_pagination and result.albums_pagination.total == 1

    def test_partial_response_only_requested_types_present(self):
        """Service skips empty/unrequested sections; model accepts subset."""
        from app.domain.schemas import CandidateSearchResult

        # Only albums requested → no artists/tracks keys at all.
        result = CandidateSearchResult.model_validate({"albums": []})

        assert result.albums == []
        assert result.artists is None
        assert result.tracks is None

    def test_response_model_exclude_none_omits_unused_sections(self):
        """The route uses response_model_exclude_none=True so wire format
        matches today's behaviour (no `null` keys for skipped types)."""
        from app.domain.schemas import CandidateSearchResult

        result = CandidateSearchResult(albums=[])
        dumped = result.model_dump(exclude_none=True)

        assert "artists" not in dumped
        assert "tracks" not in dumped
        assert dumped == {"albums": []}
