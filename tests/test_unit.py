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

    def test_raises_503_on_jwks_outage_in_prod(self, monkeypatch):
        # STAB-2 Step 4: a JWKS-fetch failure must surface 503, not an unhandled
        # 500. Token has a parseable header so it reaches _get_jwks().
        import base64
        import json

        import httpx
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        from app.core import auth, config

        def _b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

        monkeypatch.setattr(config.settings, "ENV", "prod")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "ap-northeast-2_abc123")

        def _boom():
            raise httpx.ConnectError("jwks unreachable")

        monkeypatch.setattr(auth, "_get_jwks", _boom)
        token = f'{_b64({"alg": "RS256", "kid": "x"})}.{_b64({"sub": "u"})}.sig'
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc_info:
            auth.require_cognito_token(credentials=creds)
        assert exc_info.value.status_code == 503


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


class TestAlbumItemMapperArtistName:
    """Unified-search album rows must carry artist_name. Regression: the mapper
    looked up primary_map by the raw uuid.UUID (al.id) while the repo keys it by
    str(al.id), so every album row dropped to artist_name=None site-wide."""

    def _make_album(self):
        from datetime import date
        al = MagicMock()
        al.id = uuid.uuid4()  # a real UUID object, as the ORM returns
        al.title = "Palette"
        al.release_date = date(2017, 4, 21)
        al.cover_url = None
        al.album_type = "album"
        al.spotify_id = "alb_sp"
        al.ext_refs = {}
        al.total_tracks = 10
        al.label = None
        al.popularity = 50
        al.best_new = False
        return al

    def test_artist_name_resolved_with_str_keyed_map(self):
        from app.mappers.album_mapper import AlbumItemMapper

        al = self._make_album()
        # primary_map is keyed by str(uuid) — exactly as AlbumRepository builds it.
        primary_map = {str(al.id): ("IU", "artist_sp_id")}

        rows = AlbumItemMapper.to_list([al], primary_map)

        assert rows[0].artist_name == "IU"
        assert rows[0].artist_spotify_id == "artist_sp_id"

    def test_artist_name_none_when_album_absent_from_map(self):
        from app.mappers.album_mapper import AlbumItemMapper

        al = self._make_album()
        rows = AlbumItemMapper.to_list([al], {})

        assert rows[0].artist_name is None
        assert rows[0].artist_spotify_id is None


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

    def _make_artist(self, name: str, *, photo_url=None, genres=None,
                     popularity=None, followers=None, spotify_url=None):
        ar = MagicMock()
        ar.id = uuid.uuid4()
        ar.name = name
        ar.spotify_id = f"sp_{name}"
        ar.photo_url = photo_url
        ar.genres = genres if genres is not None else []
        ar.popularity = popularity
        ar.followers = followers
        ar.spotify_url = spotify_url
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

    def test_album_artist_media_fields_mapped(self):
        # Regression: album detail dropped photo_url/genres/popularity/
        # followers/spotify_url (only id/name/spotify_id were mapped), so the
        # /profile bucket detail slide-over rendered a letter tile instead of
        # the synced artist photo even though the DB column was populated.
        al = self._make_album()
        ar = self._make_artist(
            "아이유",
            photo_url="https://i.scdn.co/image/iu.jpg",
            genres=["k-pop", "k-ballad"],
            popularity=88,
            followers=1234567,
            spotify_url="https://open.spotify.com/artist/abc",
        )
        svc = self._svc(album=al, album_artists=[ar], tracks=[])
        out = svc.get_album_detail(str(al.id)).artists[0]
        assert out.photo_url == "https://i.scdn.co/image/iu.jpg"
        assert out.genres == ["k-pop", "k-ballad"]
        assert out.popularity == 88
        assert out.followers_count == 1234567
        assert out.spotify_url == "https://open.spotify.com/artist/abc"

    def test_album_artist_media_fields_default_when_absent(self):
        al = self._make_album()
        ar = self._make_artist("Unknown")
        svc = self._svc(album=al, album_artists=[ar], tracks=[])
        out = svc.get_album_detail(str(al.id)).artists[0]
        assert out.photo_url is None
        assert out.genres == []
        assert out.popularity is None
        assert out.followers_count is None
        assert out.spotify_url is None


class TestTrackItemMapperFeatArtistNames:
    """BUG-19 Step 1: unified search TrackItem 의 feat_artist_names list 노출.

    album_service 와 변형 규칙이 다르다 — representative `artist_name` 만 제외
    (검색 응답에는 album.artists 메타가 없어 album_artist_ids 비교 불가, RFC 참조).
    """

    def _make_artist(self, name: str):
        ar = MagicMock()
        ar.id = uuid.uuid4()
        ar.name = name
        return ar

    def _make_album(self, *, artists=None):
        from datetime import date
        al = MagicMock()
        al.id = uuid.uuid4()
        al.title = "Album"
        al.release_date = date(2020, 1, 1)
        al.cover_url = None
        al.spotify_id = "alb_sp"
        al.artists = artists or []
        return al

    def _make_track(self, *, title: str, artists: list, album):
        t = MagicMock()
        t.id = uuid.uuid4()
        t.title = title
        t.track_no = 1
        t.duration_sec = 200
        t.spotify_id = "trk_sp"
        t.album_id = album.id
        t.album = album
        t.artists = artists
        return t

    def test_two_artists_primary_excluded(self):
        """track.artists=[A, B] → artist_name=A, feat=[B]."""
        from app.mappers.track_mapper import TrackItemMapper
        a = self._make_artist("Alpha")
        b = self._make_artist("Bravo")
        al = self._make_album()
        t = self._make_track(title="Song", artists=[a, b], album=al)
        items = TrackItemMapper.to_list([t])
        assert items[0].artist_name == "Alpha"
        assert items[0].feat_artist_names == ["Bravo"]

    def test_single_artist_empty_feat(self):
        """track.artists=[A] → feat=[]."""
        from app.mappers.track_mapper import TrackItemMapper
        a = self._make_artist("Alpha")
        al = self._make_album()
        t = self._make_track(title="Song", artists=[a], album=al)
        items = TrackItemMapper.to_list([t])
        assert items[0].artist_name == "Alpha"
        assert items[0].feat_artist_names == []

    def test_album_artists_fallback_no_feat(self):
        """track.artists=[], album.artists=[X] → artist_name=X, feat=[] (mapper 는
        album_artists 를 feat 후보로 쓰지 않음)."""
        from app.mappers.track_mapper import TrackItemMapper
        x = self._make_artist("Xray")
        al = self._make_album(artists=[x])
        t = self._make_track(title="Song", artists=[], album=al)
        items = TrackItemMapper.to_list([t])
        assert items[0].artist_name == "Xray"
        assert items[0].feat_artist_names == []

    def test_duplicate_primary_in_track_artists(self):
        """track.artists=[BTS, BTS] → primary 제외 후 빈 list (dedupe + 정렬)."""
        from app.mappers.track_mapper import TrackItemMapper
        bts1 = self._make_artist("BTS")
        bts2 = self._make_artist("BTS")
        al = self._make_album()
        t = self._make_track(title="Song", artists=[bts1, bts2], album=al)
        items = TrackItemMapper.to_list([t])
        assert items[0].artist_name == "BTS"
        assert items[0].feat_artist_names == []

    def test_three_artists_sorted_and_deduped(self):
        """track.artists=[A, B, A] → primary=A, feat=[B] (잔여 dedupe + 알파벳 정렬)."""
        from app.mappers.track_mapper import TrackItemMapper
        a1 = self._make_artist("Alpha")
        b = self._make_artist("Bravo")
        a2 = self._make_artist("Alpha")
        al = self._make_album()
        t = self._make_track(title="Song", artists=[a1, b, a2], album=al)
        items = TrackItemMapper.to_list([t])
        assert items[0].artist_name == "Alpha"
        assert items[0].feat_artist_names == ["Bravo"]


class TestTrackMapperPopularityOrdering:
    """BUG-19 Q1 (a): primary pick must be deterministic.

    `_sort_artists_by_popularity` orders by (popularity DESC, name ASC), so
    the most-popular collaborator becomes the primary regardless of the
    physical row order returned by Postgres (which has no `ORDER BY` on the
    `track_artists` / `album_artists` relationships).
    """

    def _make_artist(self, name: str, popularity=None):
        ar = MagicMock()
        ar.id = uuid.uuid4()
        ar.name = name
        ar.popularity = popularity
        return ar

    def _make_album(self):
        from datetime import date
        al = MagicMock()
        al.id = uuid.uuid4()
        al.title = "Album"
        al.release_date = date(2020, 1, 1)
        al.cover_url = None
        al.spotify_id = "alb_sp"
        al.artists = []
        return al

    def _make_track(self, *, artists, album):
        t = MagicMock()
        t.id = uuid.uuid4()
        t.title = "Song"
        t.track_no = 1
        t.duration_sec = 200
        t.spotify_id = "trk_sp"
        t.album_id = album.id
        t.album = album
        t.artists = artists
        return t

    def test_popular_artist_wins_regardless_of_order(self):
        from app.mappers.track_mapper import TrackItemMapper
        unpopular = self._make_artist("Aaron", popularity=10)
        popular = self._make_artist("Zelda", popularity=90)
        al = self._make_album()
        t1 = self._make_track(artists=[unpopular, popular], album=al)
        t2 = self._make_track(artists=[popular, unpopular], album=al)
        items = TrackItemMapper.to_list([t1, t2])
        assert items[0].artist_name == "Zelda"
        assert items[1].artist_name == "Zelda"
        # primary excluded from feat — feat is the other one regardless of order
        assert items[0].feat_artist_names == ["Aaron"]
        assert items[1].feat_artist_names == ["Aaron"]

    def test_null_popularity_falls_back_to_name(self):
        from app.mappers.track_mapper import TrackItemMapper
        a = self._make_artist("Bravo", popularity=None)
        b = self._make_artist("Alpha", popularity=None)
        al = self._make_album()
        t = self._make_track(artists=[a, b], album=al)
        items = TrackItemMapper.to_list([t])
        # Both null → tie broken by name ASC → Alpha is primary
        assert items[0].artist_name == "Alpha"
        assert items[0].feat_artist_names == ["Bravo"]

    def test_numeric_popularity_beats_null_popularity(self):
        from app.mappers.track_mapper import TrackItemMapper
        unknown = self._make_artist("Famous", popularity=None)
        known = self._make_artist("Niche", popularity=5)
        al = self._make_album()
        t = self._make_track(artists=[unknown, known], album=al)
        items = TrackItemMapper.to_list([t])
        # Numeric popularity (even 5) outranks null
        assert items[0].artist_name == "Niche"
        assert items[0].feat_artist_names == ["Famous"]


class TestUnifiedSearchExpansion:
    """BUG-19 Step 1: service-level merge / dedup / path-dependent ranking.

    These exercise the pure orchestration logic in `SearchService.unified_search`
    using stubbed repos. The real-engine integration test in
    `tests/integration/test_unified_search_expansion.py` covers query-count
    boundedness against live Postgres ([[feedback-sa-session-lifecycle-mock-blind]]).
    """

    def _stub_artist(self, *, name, popularity=50):
        ar = MagicMock()
        ar.id = uuid.uuid4()
        ar.name = name
        ar.popularity = popularity
        ar.followers = 1000
        ar.views = 0
        ar.spotify_id = f"sp_{name}"
        ar.photo_url = None
        ar.spotify_url = None
        ar.ext_refs = {}
        ar.genres = []
        return ar

    def _stub_album(self, *, title, popularity=50, artists=None, release=None):
        from datetime import date
        al = MagicMock()
        al.id = uuid.uuid4()
        al.title = title
        al.popularity = popularity
        al.release_date = release or date(2020, 1, 1)
        al.cover_url = None
        al.album_type = "album"
        al.spotify_id = f"alb_{title}"
        al.total_tracks = None
        al.label = None
        al.ext_refs = {}
        al.artists = artists or []
        return al

    def _stub_track(self, *, title, album, artists=None):
        t = MagicMock()
        t.id = uuid.uuid4()
        t.title = title
        t.track_no = 1
        t.duration_sec = 200
        t.spotify_id = f"trk_{title}"
        t.album_id = album.id
        t.album = album
        t.artists = artists or []
        return t

    def _build_service(self, *, literal_artists, literal_albums, literal_tracks,
                       expand_artist_albums=None, expand_artist_tracks=None,
                       expand_album_tracks=None):
        from app.services.search_service import SearchService
        svc = SearchService(MagicMock())
        svc.artist_repo = MagicMock()
        svc.album_repo = MagicMock()
        svc.track_repo = MagicMock()
        svc.artist_repo.search_by_name.return_value = literal_artists
        svc.album_repo.search_by_title.return_value = literal_albums
        svc.track_repo.search_by_title.return_value = literal_tracks
        svc.album_repo.list_by_artist_id_simple.side_effect = (
            lambda artist_id, limit: (expand_artist_albums or {}).get(artist_id, [])
        )
        svc.track_repo.list_by_artist_id.side_effect = (
            lambda artist_id, limit: (expand_artist_tracks or {}).get(artist_id, [])
        )
        svc.track_repo.list_by_album_ids.return_value = expand_album_tracks or []
        svc.album_repo.get_primary_artist_map.return_value = {}
        return svc

    def test_artist_match_expands_to_albums_and_tracks(self):
        ar = self._stub_artist(name="Solo", popularity=80)
        al = self._stub_album(title="DebutLP", popularity=60, artists=[ar])
        t = self._stub_track(title="DebutSong", album=al, artists=[ar])
        svc = self._build_service(
            literal_artists=[ar],
            literal_albums=[],
            literal_tracks=[],
            expand_artist_albums={ar.id: [al]},
            expand_artist_tracks={ar.id: [t]},
        )
        res = svc.unified_search(q="Solo", limit=20, offset=0)
        assert len(res.artists) == 1
        assert res.artists[0].name == "Solo"
        # Albums + tracks materialised even though they were never literal-matched
        assert len(res.albums) == 1 and res.albums[0].title == "DebutLP"
        assert len(res.tracks) == 1 and res.tracks[0].title == "DebutSong"

    def test_album_match_expands_to_tracks_and_pulls_in_artists(self):
        ar = self._stub_artist(name="BandX", popularity=70)
        al = self._stub_album(title="MatchedAlbum", popularity=80, artists=[ar])
        track_in_album = self._stub_track(title="Cut1", album=al, artists=[ar])
        svc = self._build_service(
            literal_artists=[],
            literal_albums=[al],
            literal_tracks=[],
            expand_album_tracks=[track_in_album],
        )
        res = svc.unified_search(q="MatchedAlbum", limit=20, offset=0)
        assert len(res.albums) == 1
        assert len(res.tracks) == 1 and res.tracks[0].title == "Cut1"
        # album.artists eager-loaded ⇒ feed expansion_artists
        assert len(res.artists) == 1 and res.artists[0].name == "BandX"

    def test_dedup_retains_literal_path_for_track_ranking(self):
        """A track that appears both as a literal title match AND via album
        expansion must rank as literal (similarity-driven), not as expansion
        (release-date-driven)."""
        from datetime import date
        ar = self._stub_artist(name="Artist", popularity=50)
        # Two albums: matched_album has a literal track; other_album expands via artist match
        matched_album = self._stub_album(title="MatchedAlbum", artists=[ar])
        other_album = self._stub_album(
            title="OtherAlbum", artists=[ar], release=date(2025, 1, 1)
        )
        # Track that literal-matches by title:
        literal_track = self._stub_track(title="MatchedAlbum", album=matched_album, artists=[ar])
        # Same track id also returned via album expansion (simulates the merge collision)
        expand_dup = literal_track
        # Newer expansion-only track (would outrank the literal track by release_date if it weren't literal)
        newer_expansion_track = self._stub_track(title="NewerCut", album=other_album, artists=[ar])
        svc = self._build_service(
            literal_artists=[ar],
            literal_albums=[matched_album],
            literal_tracks=[literal_track],
            expand_album_tracks=[expand_dup],
            expand_artist_tracks={ar.id: [newer_expansion_track]},
        )
        res = svc.unified_search(q="MatchedAlbum", limit=20, offset=0)
        # Dedup: literal_track appears once
        ids = [t.id for t in res.tracks]
        assert len(ids) == len(set(ids)), "track ids must be deduped"
        # Literal-path track ranks before the (newer) expansion-only track
        assert res.tracks[0].title == "MatchedAlbum"

    def test_one_hop_only_no_multi_hop_album_fan_out(self):
        """An album match must not transitively expand into "other albums by
        the same artist". Only the matched album's tracks + artists surface."""
        ar = self._stub_artist(name="DeepCatalog", popularity=50)
        matched_album = self._stub_album(title="MatchedAlbum", artists=[ar])
        # other_album exists for the artist but must NOT appear (would be 2-hop)
        other_album = self._stub_album(title="UnrelatedAlbum", artists=[ar])
        svc = self._build_service(
            literal_artists=[],
            literal_albums=[matched_album],
            literal_tracks=[],
            # Even if the artist repo happened to be called, we expect it not to be —
            # so explicit expand maps are empty.
            expand_artist_albums={ar.id: [other_album]},
        )
        res = svc.unified_search(q="MatchedAlbum", limit=20, offset=0)
        album_titles = {a.title for a in res.albums}
        assert "MatchedAlbum" in album_titles
        assert "UnrelatedAlbum" not in album_titles, "2-hop expansion leaked"
        # And the artist's list_by_artist_id_simple must not have been called:
        svc.album_repo.list_by_artist_id_simple.assert_not_called()

    def test_per_bucket_offset_overrides_singular_offset(self):
        ar = self._stub_artist(name="A", popularity=10)
        svc = self._build_service(
            literal_artists=[ar],
            literal_albums=[],
            literal_tracks=[],
        )
        svc.unified_search(
            q="A", limit=20, offset=5,
            artist_offset=100, album_offset=None, track_offset=None,
        )
        # artist_offset override wins
        svc.artist_repo.search_by_name.assert_called_with("A", 20, 100)
        # album/track buckets fall back to singular offset=5
        svc.album_repo.search_by_title.assert_called_with("A", 20, 5)
        svc.track_repo.search_by_title.assert_called_with("A", 20, 5)

    def test_type_filter_skips_excluded_buckets(self):
        ar = self._stub_artist(name="ArtistOnly", popularity=10)
        svc = self._build_service(
            literal_artists=[ar],
            literal_albums=[],
            literal_tracks=[],
        )
        res = svc.unified_search(q="ArtistOnly", limit=20, offset=0, types={"artist"})
        assert res.albums == []
        assert res.tracks == []
        # No expansion fired into excluded buckets
        svc.album_repo.list_by_artist_id_simple.assert_not_called()
        svc.track_repo.list_by_artist_id.assert_not_called()

    def test_expansion_only_artists_ranked_by_popularity(self):
        from datetime import date
        # Album literal-matches; two artists pulled in via expansion
        low_pop = self._stub_artist(name="LowPop", popularity=10)
        high_pop = self._stub_artist(name="HighPop", popularity=90)
        al = self._stub_album(
            title="Compilation", popularity=60, artists=[low_pop, high_pop],
            release=date(2020, 1, 1),
        )
        svc = self._build_service(
            literal_artists=[],
            literal_albums=[al],
            literal_tracks=[],
        )
        res = svc.unified_search(q="Compilation", limit=20, offset=0)
        # Both are expansion-only — ranked by popularity DESC
        assert [a.name for a in res.artists] == ["HighPop", "LowPop"]


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
