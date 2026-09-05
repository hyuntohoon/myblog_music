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
    YouTubeRateLimited,
)
from app.core.ids import parse_uuid_or_404
from myblog_shared_db.models import (
    Album,
    Artist as TrackArtist,
    Track,
    album_artists_table,
    track_artists_table,
)
from sqlalchemy import select
from sqlalchemy.orm import aliased

# Two aliases of the same table: a track's own performer and its album's, joined
# in one statement so the fallback needs no second round trip and no lazy load
# after the session is closed.
AlbumArtist = aliased(TrackArtist, name="album_artist")


def _seconds_until_quota_reset() -> int:
    """Seconds until midnight US/Pacific, when the daily YouTube pool resets.

    A `Retry-After` a client can act on. Computed rather than a constant so it
    shrinks as the day goes on; floored at 60 so a caller never reads "retry
    immediately" for a budget that has not reset yet.
    """
    from datetime import datetime, timedelta, timezone

    # Pacific is UTC-8 (PST) or UTC-7 (PDT). The wider offset is used
    # deliberately: an over-estimate delays a retry, an under-estimate causes a
    # retry that burns nothing but returns the same 429.
    pacific = timezone(timedelta(hours=-8))
    now = datetime.now(pacific)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((midnight - now).total_seconds()))
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
# the ordering by misreporting it, and the query is built in one place.
#
# On the workspace invariant "never a synchronous Spotify call in a user-facing
# endpoint": this endpoint is NOT an exception to it and does not reinterpret
# it. It lives in MUSIC, alongside the standing Spotify `/candidates` read
# above, and the invariant's purpose — stated in CLAUDE.md as "backend <-> music
# separation exists so a music-provider outage cannot affect posts" — is served
# by that placement. A YouTube outage degrades music discovery and cannot reach
# posts. Nothing here writes: no DB write, no queue.
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
    # A non-UUID must never reach psycopg — it raises InvalidTextRepresentation
    # and the route 500s. Same defect class as AUDIT-2026-07-26 A-3, same helper,
    # and this route is in tests/test_malformed_ids.py alongside the original four.
    track_uuid = parse_uuid_or_404(track_id, detail="track not found")

    # FETCH -> MATERIALIZE -> CLOSE -> EXTERNAL WORK. The outbound YouTube calls
    # below must NOT run inside this request's transaction: a session held open
    # across an external API call is the recurring bug class that produced the
    # Neon ProtocolViolation (workspace CLAUDE.md). Everything the response and
    # the search need is copied into plain values here, and the session is
    # released before the first HTTP call.
    row = db.execute(
        select(Track.id, Track.title, Track.duration_sec, TrackArtist.name, AlbumArtist.name)
        .select_from(Track)
        .outerjoin(track_artists_table, track_artists_table.c.track_id == Track.id)
        .outerjoin(TrackArtist, TrackArtist.id == track_artists_table.c.artist_id)
        .outerjoin(Album, Album.id == Track.album_id)
        .outerjoin(album_artists_table, album_artists_table.c.album_id == Album.id)
        .outerjoin(AlbumArtist, AlbumArtist.id == album_artists_table.c.artist_id)
        .where(Track.id == track_uuid)
        .limit(1)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="track not found")
    track_id_str, title, duration_sec, track_artist, album_artist = (
        str(row[0]), row[1], row[2], row[3], row[4],
    )
    # Track artists first, album artists as the fallback — a compilation track
    # carries its performer on the track, not on the album, and the Phase 0-A
    # probe matched the classical and jazz compilations through exactly that.
    artist_name = track_artist or album_artist

    # The transaction ends HERE, before any network call. Explicit rather than
    # left to the dependency's `finally`, which only runs after the response is
    # built — i.e. after the HTTP calls below.
    db.close()

    service = YouTubeCandidateService()
    try:
        query, candidates = service.find_candidates(
            title=title,
            artist_name=artist_name,
            track_duration_sec=duration_sec,
            max_results=limit or settings.YOUTUBE_SEARCH_MAX_RESULTS,
        )
    except YouTubeQuotaExhausted as e:
        # 429 and NOT 503: the request was valid and the service is healthy —
        # the DAILY discovery budget is spent. The client must not retry; there
        # is nothing to retry into until the pool resets.
        # Headers go ON the exception. An injected `Response`'s headers are
        # merged only into a RETURNED response; FastAPI builds a fresh one for a
        # raised HTTPException and the mutation is silently lost.
        raise HTTPException(
            status_code=429,
            detail=f"youtube_quota_exhausted: {e}. Discovery quota resets at midnight Pacific.",
            headers={"Retry-After": str(_seconds_until_quota_reset())},
        )
    except YouTubeRateLimited as e:
        # Also 429, but a DIFFERENT condition with different advice: a
        # short-window rate limit clears in seconds, and this is the one case
        # where retrying shortly is the correct client behaviour. Telling a
        # member to come back tomorrow here would be false.
        raise HTTPException(
            status_code=429,
            detail=f"youtube_rate_limited: {e}. Short-window limit — retry in a moment.",
            headers={"Retry-After": "30"},
        )
    except YouTubeNotConfigured:
        # Fail closed, and say which half is missing without naming the value.
        raise HTTPException(status_code=503, detail="youtube_not_configured")
    except YouTubeError as e:
        raise HTTPException(status_code=502, detail=f"youtube_upstream_error: {e}")

    return {
        "track_id": track_id_str,
        "query": query,
        "track_duration_sec": duration_sec,
        "candidates": candidates,
    }
