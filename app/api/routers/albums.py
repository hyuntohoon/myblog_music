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

@router.post("/sync", response_model=AlbumDetail)
def sync_album(payload: SyncAlbumIn = Body(...), db: Session = Depends(get_db)):
    # 사용자가 후보를 '확정'했을 때 호출. Spotify에서 앨범/트랙 전량 동기화 후 DB 기준으로 반환.
    svc = AlbumService(db)
    return svc.sync_album_by_spotify(payload.spotify_album_id, market=payload.market)

@router.get("/by-spotify/{spotify_album_id}", response_model=AlbumDetail)
def get_album_by_spotify(spotify_album_id: str = Path(...), db: Session = Depends(get_db)):
    svc = AlbumService(db)
    return svc.get_album_detail_by_spotify(spotify_album_id)