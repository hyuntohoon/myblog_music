from __future__ import annotations

from fastapi import APIRouter, Depends, Query, HTTPException, Response
from sqlalchemy.orm import Session
from typing import Optional

from app.core.cache import SEARCH_CACHE_CONTROL
from app.core.db import get_db
from app.domain.schemas import CandidateSearchResult, UnifiedSearchResult
from app.services.search_service import SearchService as DBSearchService

from app.clients.sqs_client import SqsClient
from app.core.auth import require_cognito_token
from app.services.cadidate_search_service import CandidateSearchService
from app.services.search_service import ALLOWED_TYPES

router = APIRouter()


# 통합 검색(DB-first) — type 필터 옵션 (default: 전체)
@router.get("/unified", response_model=UnifiedSearchResult, summary="통합 검색(DB-first)")
def unified_search(
    q: str = Query(..., min_length=1, description="검색어"),
    type: str = Query(
        "album,artist,track",
        description='검색 대상 (콤마 조합 허용): "album", "artist", "track"',
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, description="Fallback offset applied to any bucket without an explicit override."),
    artist_offset: Optional[int] = Query(None, ge=0, description="Per-bucket offset for the artists slice (overrides `offset`)."),
    album_offset: Optional[int] = Query(None, ge=0, description="Per-bucket offset for the albums slice (overrides `offset`)."),
    track_offset: Optional[int] = Query(None, ge=0, description="Per-bucket offset for the tracks slice (overrides `offset`)."),
    explain: bool = Query(False, description="Dev triage: include per-row ranking debug under `debug` (default response shape is otherwise unchanged)."),
    response: Response = None,  # type: ignore[assignment]  # injected by FastAPI
    db: Session = Depends(get_db),
):
    types = {t.strip().lower() for t in type.split(",") if t.strip()}
    invalid = types - ALLOWED_TYPES
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid types: {sorted(invalid)}")
    if not types:
        raise HTTPException(status_code=400, detail="type must not be empty")
    result = DBSearchService(db).unified_search(
        q=q,
        types=types,
        limit=limit,
        offset=offset,
        artist_offset=artist_offset,
        album_offset=album_offset,
        track_offset=track_offset,
        explain=explain,
    )
    # 200-only: validation 400s above raise before reaching here, so they stay uncached.
    response.headers["Cache-Control"] = SEARCH_CACHE_CONTROL
    return result


# -------------------------------
# 후보 검색 (+ 앨범 동기화 enqueue) - 기존 유지
# -------------------------------
@router.get(
    "/candidates",
    response_model=CandidateSearchResult,
    response_model_exclude_none=True,
    summary="Spotify 후보 검색(읽기 전용 + 앨범 동기화 enqueue)",
)
def search_candidates(
    q: str = Query(..., description="Spotify 검색 쿼리"),
    type: str = Query("album,artist,track", description='허용: "album,artist,track" 중 조합'),
    market: Optional[str] = Query(None, description="예: KR, US"),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0, le=1000),
    db: Session = Depends(get_db),
    include_external: Optional[str] = Query(None, description='선택값: "audio"'),
    _claims: dict = Depends(require_cognito_token),
):
    if include_external not in (None, "audio"):
        raise HTTPException(status_code=400, detail='include_external must be "audio" or omitted')

    service = CandidateSearchService(db=db, sqs=SqsClient())
    try:
        return service.search_candidates(
            q=q, typ=type, market=market, limit=limit, offset=offset, include_external=include_external
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))