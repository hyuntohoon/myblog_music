from fastapi import APIRouter, Path, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.artist_service import ArtistService
from app.domain.schemas import SearchResult
from app.repositories.album_repo import AlbumRepository

router = APIRouter()


@router.get("/{artist_id}/albums", response_model=SearchResult)
def get_artist_albums(
    artist_id: str = Path(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    album_repo = AlbumRepository(db)
    svc = ArtistService(db, album_repo)
    return svc.list_albums_by_artist(artist_id=artist_id, limit=limit, offset=offset)