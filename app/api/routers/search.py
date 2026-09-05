from __future__ import annotations

from fastapi import APIRouter, Depends, Query, HTTPException, Response
from sqlalchemy.orm import Session
from typing import Optional

from app.core.cache import SEARCH_CACHE_CONTROL
from app.core.config import settings
from app.core.db import get_db
from app.domain.schemas import (
    CandidateSearchResult,
    UnifiedSearchResult,
    YouTubeCandidateSearchResult,
)
from app.services.search_service import SearchService as DBSearchService

from app.core.auth import require_cognito_token
from app.services.cadidate_search_service import CandidateSearchService
from app.services.youtube_candidate_service import YouTubeCandidateService
from app.clients.youtube_client import (
    YouTubeError,
    YouTubeNotConfigured,
    YouTubeQuotaExhausted,
)
from myblog_shared_db.models import Track
from sqlalchemy import select
from sqlalchemy.orm import selectinload
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
# Spotify 후보 검색. Queue dispatch is an explicit POST under /sync-requests.
# -------------------------------
@router.get(
    "/candidates",
    response_model=CandidateSearchResult,
    response_model_exclude_none=True,
    summary="Spotify 후보 검색(읽기 전용)",
)
def search_candidates(
    q: str = Query(..., description="Spotify 검색 쿼리"),
    type: str = Query("album,artist,track", description='허용: "album,artist,track" 중 조합'),
    market: Optional[str] = Query(None, description="예: KR, US"),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0, le=1000),
    include_external: Optional[str] = Query(None, description='선택값: "audio"'),
    _claims: dict = Depends(require_cognito_token),
):
    if include_external not in (None, "audio"):
        raise HTTPException(status_code=400, detail='include_external must be "audio" or omitted')

    service = CandidateSearchService()
    try:
        return service.search_candidates(
            q=q, typ=type, market=market, limit=limit, offset=offset, include_external=include_external
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


# -------------------------------
# FEAT-youtube-playback-provider Step A2 — YouTube candidate search.
#
# The track is identified by CATALOG id, not by a caller-supplied title and
# duration. That is what makes the ranking trustworthy: the duration the
# candidates are ranked against comes from `tracks`, so a caller cannot shift
# the ordering by misreporting it, and the query is built from one place.
#
# Synchronous outbound call in a user-facing endpoint, deliberately, and it is
# the same shape as the Spotify `/candidates` read above — a discovery read the
# member is waiting on. The "never a synchronous Spotify call in a user-facing
# endpoint" invariant is about the WRITE path (candidates -> SQS -> worker), and
# nothing here writes: this endpoint has no DB write and no queue.
# -------------------------------
@router.get(
    "/youtube-candidates",
    response_model=YouTubeCandidateSearchResult,
    summary="YouTube 후보 검색(읽기 전용)",
)
def search_youtube_candidates(
    track_id: str = Query(..., description="카탈로그 트랙 UUID"),
    limit: Optional[int] = Query(None, ge=1, le=25, description="후보 수 (기본: 설정값)"),
    db: Session = Depends(get_db),
    _claims: dict = Depends(require_cognito_token),
):
    track = db.execute(
        select(Track)
        .options(selectinload(Track.artists), selectinload(Track.album))
        .where(Track.id == track_id)
    ).scalars().first()
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")

    # Track artists first, album artists as the fallback — a compilation track
    # carries its performer on the track, not on the album, and the Phase 0-A
    # probe matched classical/jazz compilations through exactly that name.
    artist_name = None
    if track.artists:
        artist_name = track.artists[0].name
    elif track.album is not None and getattr(track.album, "artists", None):
        artist_name = track.album.artists[0].name

    service = YouTubeCandidateService()
    try:
        candidates = service.find_candidates(
            title=track.title,
            artist_name=artist_name,
            track_duration_sec=track.duration_sec,
            max_results=limit or settings.YOUTUBE_SEARCH_MAX_RESULTS,
        )
    except YouTubeQuotaExhausted as e:
        # A distinct, printable status. 429 and NOT 503: the request was valid
        # and the service is healthy — the daily discovery budget is spent, and
        # it resets. The client must not retry; there is nothing to retry into.
        raise HTTPException(
            status_code=429,
            detail=f"youtube_quota_exhausted: {e}. Discovery quota resets at midnight Pacific.",
        )
    except YouTubeNotConfigured:
        # Fail closed, and say which half is missing without naming the value.
        raise HTTPException(status_code=503, detail="youtube_not_configured")
    except YouTubeError as e:
        raise HTTPException(status_code=502, detail=f"youtube_upstream_error: {e}")

    return {
        "track_id": str(track.id),
        "query": service.build_query(title=track.title, artist_name=artist_name),
        "track_duration_sec": track.duration_sec,
        "candidates": candidates,
    }
