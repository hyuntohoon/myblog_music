from __future__ import annotations

from sqlalchemy.orm import Session
from app.repositories.artist_repo import ArtistRepository
from app.repositories.album_repo import AlbumRepository
from app.domain.schemas import SearchResult, ArtistItem, AlbumItem
from app.clients.spotify_client import spotify


class SearchService:
    def __init__(self, db: Session):
        self.db = db
        self.artist_repo = ArtistRepository(db)
        self.album_repo = AlbumRepository(db)

    def basic_search(self, *, mode: str, q: str, limit: int, offset: int) -> SearchResult:
        """
        기본 검색은 내부 DB만 조회.
        - mode == "artist": 아티스트명 like 검색
        - mode == "album" : 앨범명 like 검색 (+대표 아티스트 1명 매핑)
        응답 스키마의 id는 문자열로 강제 변환(str)한다.
        """
        if mode == "artist":
            artists = self.artist_repo.search_by_name(q, limit, offset)
            items = [
                ArtistItem(
                    id=str(a.id),
                    name=a.name,
                    spotify_id=a.spotify_id,
                    cover_url=a.photo_url,
                )
                for a in artists
            ]
            return SearchResult(type="artist", items=items)

        # default: album
        albums = self.album_repo.search_by_title(q, limit, offset)

        # 앨범들의 대표 아티스트 맵 (album_id -> (artist_name, artist_spotify_id))
        album_ids = [str(al.id) for al in albums]
        primary_map = self.album_repo.get_primary_artist_map(album_ids)

        items = [
            AlbumItem(
                id=str(al.id),
                title=al.title,
                release_date=al.release_date.isoformat() if al.release_date else None,
                cover_url=al.cover_url,
                album_type=al.album_type,
                spotify_id=al.spotify_id,
                artist_name=(primary_map.get(str(al.id)) or (None, None))[0],
                artist_spotify_id=(primary_map.get(str(al.id)) or (None, None))[1],
            )
            for al in albums
        ]
        return SearchResult(type="album", items=items)

    def external_candidates(self, *, mode: str, q: str, artist: str | None, limit: int) -> SearchResult:
        """
        추가 조회(외부)는 정책상 현재 앨범만 지원.
        Spotify 검색 결과를 얇게 매핑해서 후보만 반환한다.
        """
        if mode != "album":
            return SearchResult(type=mode, items=[])

        data = spotify.search_albums(album=q, artist=artist, limit=limit) or {}
        albums = (data.get("albums") or {}).get("items") or []

        items: list[AlbumItem] = []
        for it in albums:
            artists_in_item = it.get("artists") or []
            first_artist = artists_in_item[0] if artists_in_item else {}

            images = it.get("images") or []
            cover_url = images[0].get("url") if images else None

            items.append(
                AlbumItem(
                    id="",  # 아직 DB에 없음 → 빈 문자열로 표시 (클라이언트가 spotify_id로 확정 호출)
                    title=it.get("name"),
                    release_date=it.get("release_date"),
                    cover_url=cover_url,
                    album_type=it.get("album_type"),
                    spotify_id=it.get("id"),
                    # 아래 두 필드는 AlbumItem에 optional로 선언되어 있어야 함
                    artist_name=first_artist.get("name"),
                    artist_spotify_id=first_artist.get("id"),
                )
            )

        return SearchResult(type="album", items=items)