from fastapi import APIRouter, Path, Depends, Body, Response, Query
from sqlalchemy.orm import Session
from app.core.cache import DETAIL_CACHE_CONTROL, SEARCH_CACHE_CONTROL
from app.core.db import get_db
from app.core.ids import parse_uuid_or_404
from app.services.album_service import AlbumService
from app.domain.schemas import AlbumDetail, SyncAlbumIn, OnThisDayResult

router = APIRouter()


# NB: declared BEFORE "/{album_id}" so FastAPI doesn't capture "on-this-day"
# as an album id. Public DB-only read (edge_guard; no JWT / no apigateway route).
@router.get(
    "/on-this-day",
    response_model=OnThisDayResult,
    summary="오늘, 이 앨범들 (같은 월/일에 과거 발매된 앨범)",
)
def albums_on_this_day(
    response: Response,
    limit: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_db),
):
    result = AlbumService(db).get_on_this_day(limit=limit)
    # date-derived (rolls at midnight); short cache like search.
    response.headers["Cache-Control"] = SEARCH_CACHE_CONTROL
    return result


@router.get("/{album_id}", response_model=AlbumDetail)
def get_album(response: Response, album_id: str = Path(...), db: Session = Depends(get_db)):
    svc = AlbumService(db)
    # A-3: catalog ids are uuid columns — parse here or Postgres 500s on the junk.
    detail = svc.get_album_detail(parse_uuid_or_404(album_id, detail="album not found"))
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