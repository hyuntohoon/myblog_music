# app/services/artist_service.py
from sqlalchemy.orm import Session
from app.repositories.album_repo import AlbumRepository
from app.domain.schemas import SearchResult
from app.mappers.album_mapper import AlbumItemMapper
from app.repositories.artist_repo import ArtistRepository

class ArtistService:
    def __init__(self, db: Session, album_repo: AlbumRepository) -> None:
        self.db = db
        self.album_repo = album_repo
        self.artist_repo = ArtistRepository(db)

    def list_albums_by_artist(
        self,
        *,
        artist_id: str,
        limit: int,
        offset: int,
    ) -> SearchResult:
        albums, primary_map = self.album_repo.list_by_artistId_artist(
            artist_id=artist_id,
            limit=limit,
            offset=offset,
        )
        items = AlbumItemMapper.to_list(albums, primary_map)
        return SearchResult(type="album", items=items)

    def list_albums_by_spotify_artist(
        self,
        *,
        spotify_id: str,
        limit: int,
        offset: int,
    ) -> SearchResult:
        # 1) spotify_id로 DB에 등록된 artist가 있는지 확인
        artist = self.artist_repo.get_by_spotify_id(spotify_id)
        albums, primary_map = self.album_repo.list_by_artistId_artist(
            artist_id=artist.id,
            limit=limit,
            offset=offset,
        )
        items = AlbumItemMapper.to_list(albums, primary_map)
        return SearchResult(type="album", items=items)