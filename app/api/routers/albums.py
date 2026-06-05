from fastapi import APIRouter, Path, Depends, Body, Response
from sqlalchemy.orm import Session
from app.core.cache import DETAIL_CACHE_CONTROL
from app.core.db import get_db
from app.services.album_service import AlbumService
from app.domain.schemas import AlbumDetail, SyncAlbumIn

router = APIRouter()

@router.get("/{album_id}", response_model=AlbumDetail)
def get_album(response: Response, album_id: str = Path(...), db: Session = Depends(get_db)):
    svc = AlbumService(db)
    detail = svc.get_album_detail(album_id)  # raises 404 before we set the header
    response.headers["Cache-Control"] = DETAIL_CACHE_CONTROL
    return detail

@router.get("/by-spotify/{spotify_album_id}", response_model=AlbumDetail)
def get_album_by_spotify(response: Response, spotify_album_id: str = Path(...), db: Session = Depends(get_db)):
    # by-spotify can 404 while the worker is still absorbing; the 404 path raises
    # in the service, so the Cache-Control below is reached on success only.
    svc = AlbumService(db)
    detail = svc.get_album_detail_by_spotify(spotify_album_id)
    response.headers["Cache-Control"] = DETAIL_CACHE_CONTROL
    return detail