from fastapi import APIRouter, Path, Depends, Body
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.services.album_service import AlbumService
from app.domain.schemas import AlbumDetail, SyncAlbumIn

router = APIRouter()

@router.get("/{album_id}", response_model=AlbumDetail)
def get_album(album_id: str = Path(...), db: Session = Depends(get_db)):
    svc = AlbumService(db)
    return svc.get_album_detail(album_id)

@router.get("/by-spotify/{spotify_album_id}", response_model=AlbumDetail)
def get_album_by_spotify(spotify_album_id: str = Path(...), db: Session = Depends(get_db)):
    svc = AlbumService(db)
    return svc.get_album_detail_by_spotify(spotify_album_id)