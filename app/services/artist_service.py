from sqlalchemy.orm import Session
from app.repositories.album_repo import AlbumRepository
from app.domain.schemas import SearchResult

class ArtistService:
    def __init__(self, db: Session):
        self.db = db
        self.albums = AlbumRepository(db)

    def list_albums_by_artist(self, *, artist_id: str, limit: int, offset: int) -> SearchResult:
        # TODO: Implement join against album_artists table to fetch albums for artist_id
        return SearchResult(type="album", items=[])
