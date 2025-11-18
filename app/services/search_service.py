from __future__ import annotations

from sqlalchemy.orm import Session
from app.repositories.artist_repo import ArtistRepository
from app.repositories.album_repo import AlbumRepository
from app.domain.schemas import SearchResult
from app.clients.spotify_client import spotify
from app.mappers.album_mapper import AlbumItemMapper
from app.mappers.artist_mapper import ArtistItemMapper
from app.mappers.album_candidate_mapper import AlbumCandidateMapper

ALLOWED_TYPES: Set[str] = {"album", "artist", "track"}

class SearchService:
    def __init__(self, db: Session):
        self.db = db
        self.artist_repo = ArtistRepository(db)
        self.album_repo = AlbumRepository(db)

    def basic_search(self, *, mode: str, q: str, limit: int, offset: int) -> SearchResult:
        mode = (mode or "album").lower()
        handlers = {
            "artist": self._handle_artist_search,
            "album": self._handle_album_search,
        }
        handler = handlers.get(mode, self._handle_album_search)
        return handler(q, limit, offset)

    # ---------------- 내부 전용 ---------------- #

    def _handle_artist_search(self, q: str, limit: int, offset: int) -> SearchResult:
        artists = self.artist_repo.search_by_name(q, limit, offset)
        items = ArtistItemMapper.to_list(artists)
        return SearchResult(type="artist", items=items)

    def _handle_album_search(self, q: str, limit: int, offset: int) -> SearchResult:
        albums = self.album_repo.search_by_title(q, limit, offset)
        primary_map = self._primary_map_for(albums)
        items = AlbumItemMapper.to_list(albums, primary_map)
        return SearchResult(type="album", items=items)

    def _primary_map_for(self, albums: list) -> dict[str, tuple[str | None, str | None]]:
        if not albums:
            return {}
        album_ids = [al.id for al in albums]
        return self.album_repo.get_primary_artist_map(album_ids)

    def external_candidates(self, *, mode: str, q: str, artist: str | None, limit: int) -> SearchResult:
        # 현재 정책상 album만 지원
        if mode.lower() != "album":
            return SearchResult(type=mode, items=[])

        data = spotify.search_albums(album=q, artist=artist, limit=limit) or {}
        albums = (data.get("albums") or {}).get("items") or []

        items = AlbumCandidateMapper.to_list(albums)
        return SearchResult(type="album", items=items)
    
