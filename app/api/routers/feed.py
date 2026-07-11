from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.cache import SEARCH_CACHE_CONTROL
from app.core.db import get_db
from app.domain.schemas import NewReleasesResult
from app.services.feed_service import FeedService

router = APIRouter()


# FEAT-release-calendar Track A: public DB-only read off the daily album_ingest
# catalog (edge_guard; no JWT / no apigateway route — GET rides the catch-all).
# No Spotify call on this path (hard rule #9).
@router.get(
    "/new-releases",
    response_model=NewReleasesResult,
    summary="새 앨범 피드 (최근 발매 정규 앨범, 아티스트 인기순)",
)
def new_releases(
    response: Response,
    days: int = Query(30, ge=1, le=90, description="발매 윈도우 (일)"),
    limit: int = Query(12, ge=1, le=50),
    db: Session = Depends(get_db),
):
    result = FeedService(db).get_new_releases(days=days, limit=limit)
    # feed rolls daily; short cache like search.
    response.headers["Cache-Control"] = SEARCH_CACHE_CONTROL
    return result
