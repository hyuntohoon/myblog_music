from __future__ import annotations

from typing import Set
from sqlalchemy.orm import Session

from app.repositories.artist_repo import ArtistRepository
from app.repositories.album_repo import AlbumRepository
from app.repositories.track_repo import TrackRepository

from app.domain.schemas import UnifiedSearchResult

from app.mappers.album_mapper import AlbumItemMapper
from app.mappers.artist_mapper import ArtistItemMapper
from app.mappers.track_mapper import TrackItemMapper

ALLOWED_TYPES: Set[str] = {"album", "artist", "track"}


class SearchService:
    def __init__(self, db: Session):
        self.db = db
        self.artist_repo = ArtistRepository(db)
        self.album_repo = AlbumRepository(db)
        self.track_repo = TrackRepository(db, self.artist_repo)

    # 통합 검색(DB): artists + albums + tracks. `types` 인자로 일부만 조회 가능 (default 전체).
    def unified_search(
        self,
        *,
        q: str,
        limit: int,
        offset: int,
        types: Set[str] | None = None,
    ) -> UnifiedSearchResult:
        wanted = types if types is not None else ALLOWED_TYPES

        artists = self.artist_repo.search_by_name(q, limit, offset) if "artist" in wanted else []
        albums = self.album_repo.search_by_title(q, limit, offset) if "album" in wanted else []
        tracks = self.track_repo.search_by_title(q, limit, offset) if "track" in wanted else []

        primary_map = self._primary_map_for(albums)

        return UnifiedSearchResult(
            artists=ArtistItemMapper.to_list(artists),
            albums=AlbumItemMapper.to_list(albums, primary_map),
            tracks=TrackItemMapper.to_list(tracks),
        )

    # ---------------- 내부 전용 ---------------- #

    def _primary_map_for(self, albums: list) -> dict[str, tuple[str | None, str | None]]:
        if not albums:
            return {}
        album_ids = [al.id for al in albums]
        return self.album_repo.get_primary_artist_map(album_ids)