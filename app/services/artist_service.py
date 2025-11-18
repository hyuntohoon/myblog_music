# app/services/artist_service.py
from sqlalchemy.orm import Session
from app.repositories.album_repo import AlbumRepository
from app.domain.schemas import SearchResult
from app.mappers.album_mapper import AlbumItemMapper

class ArtistService:
    def __init__(self, db: Session, album_repo: AlbumRepository) -> None:
        self.db = db
        self.album_repo = album_repo

    def list_albums_by_artist(
        self,
        *,
        artist_id: str,
        limit: int,
        offset: int,
    ) -> SearchResult:
        albums, primary_map = self.album_repo.list_by_artist(
            artist_id=artist_id,
            limit=limit,
            offset=offset,
        )
        items = AlbumItemMapper.to_list(albums, primary_map)
        return SearchResult(type="album", items=items)