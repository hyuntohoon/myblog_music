from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List

from app.core.db import get_db
from app.services.search_service import SearchService
from app.domain.schemas import SearchResult
from app.clients.spotify_client import spotify

router = APIRouter()

# Spotify 후보 검색에서 허용할 타입만 제한 (album/artist/track)
ALLOWED_TYPES = {"album", "artist", "track"}


# =========================
# 1) 기본 검색 (DB 우선)
#    GET /api/search?mode=artist|album&q=...&limit=&offset=
# =========================
@router.get("", response_model=SearchResult, summary="기본 검색(DB)")
def basic_search(
    mode: str = Query(..., description='검색 모드: "artist" 또는 "album"'),
    q: str = Query(..., min_length=1, description="검색어"),
    limit: int = Query(20, ge=1, le=100, description="페이지 크기(1~100)"),
    offset: int = Query(0, ge=0, description="오프셋(0부터)"),
    db: Session = Depends(get_db),
):
    """
    내부 DB 기준의 기본 검색.  
    - mode=artist : 아티스트명 부분일치  
    - mode=album  : 앨범명 부분일치
    """
    if mode not in {"artist", "album"}:
        raise HTTPException(status_code=400, detail='mode must be "artist" or "album"')

    svc = SearchService(db)
    return svc.basic_search(mode=mode, q=q, limit=limit, offset=offset)


# =========================
# 2) 후보 검색 (외부/Spotify, 읽기 전용)
#    GET /api/search/candidates?q=...&type=album,artist,track&market=KR&limit=10&offset=0&include_external=audio
# =========================
@router.get("/candidates", summary="Spotify 후보 검색(읽기 전용)")
def search_candidates(
    q: str = Query(
        ...,
        description=(
            "검색 쿼리 문자열 (필터 포함 가능: album:, artist:, track:, year:, isrc:, genre:, "
            "upc:, tag:new, tag:hipster 등. 예: track:Doxy artist:\"Miles Davis\" year:1955-1960)"
        ),
    ),
    type: str = Query(
        "album,artist,track",
        description='검색 타입(쉼표구분). 허용: "album,artist,track" 중 조합',
    ),
    market: Optional[str] = Query(None, description="ISO-3166-1 alpha-2 국가코드 (예: KR, US, ES)"),
    limit: int = Query(10, ge=1, le=50, description="결과 개수 (1~50)"),
    offset: int = Query(0, ge=0, le=1000, description="페이지 오프셋 (0~1000)"),
    include_external: Optional[str] = Query(
        None,
        description='선택값: "audio"만 허용. 외부 호스팅 오디오 컨텐츠를 재생 가능 표시.',
    ),
    db: Session = Depends(get_db),  # 의존성 형태만 유지 (DB 미사용)
):
    """
    - **DB에 저장하지 않는** 후보 검색 API.  
    - Spotify의 `/v1/search`를 거의 그대로 감싸되 **album/artist/track만** 지원.  
    - UI는 이 응답을 렌더링 → 사용자가 **정확한 앨범을 선택** → `/api/albums/sync`로 확정 동기화.
    """
    # include_external 검증
    if include_external not in (None, "audio"):
        raise HTTPException(status_code=400, detail='include_external must be "audio" or omitted')

    # type 정제 및 검증
    raw_types = [t.strip().lower() for t in type.split(",") if t.strip()]
    wanted = [t for t in raw_types if t in ALLOWED_TYPES]
    if not wanted:
        raise HTTPException(
            status_code=400,
            detail=f"type must include at least one of {sorted(ALLOWED_TYPES)}",
        )

    # Spotify 검색 호출 (※ spotify.search 는 type= 콤마문자열만 받음)
    type_str = ",".join(wanted)
    data = spotify.search(
        q=q,
        type=type_str,
        market=market,
        limit=limit,
        offset=offset,
        include_external=include_external,
    )

    # 결과 매핑 (UI용 경량화)
    out: Dict[str, Any] = {}

    if "albums" in data and data["albums"]:
        out["albums"] = [_map_album_item(a) for a in (data["albums"].get("items") or [])]
        out["albums_pagination"] = _page_info(data["albums"])

    if "artists" in data and data["artists"]:
        out["artists"] = [_map_artist_item(a) for a in (data["artists"].get("items") or [])]
        out["artists_pagination"] = _page_info(data["artists"])

    if "tracks" in data and data["tracks"]:
        out["tracks"] = [_map_track_item(t) for t in (data["tracks"].get("items") or [])]
        out["tracks_pagination"] = _page_info(data["tracks"])

    return out


# ---------- 헬퍼들 ----------

def _page_info(block: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "total": block.get("total"),
        "limit": block.get("limit"),
        "offset": block.get("offset"),
        "next": block.get("next"),
        "previous": block.get("previous"),
        "href": block.get("href"),
    }


def _map_album_item(a: Dict[str, Any]) -> Dict[str, Any]:
    images = a.get("images") or []
    cover = images[0]["url"] if images else None
    primary_artist = (a.get("artists") or [{}])[0]
    return {
        "spotify_id": a.get("id"),
        "title": a.get("name"),
        "album_type": a.get("album_type"),
        "release_date": a.get("release_date"),
        "cover_url": cover,
        "artist_name": primary_artist.get("name"),
        "artist_spotify_id": primary_artist.get("id"),
        "external_url": (a.get("external_urls") or {}).get("spotify"),
    }


def _map_artist_item(ar: Dict[str, Any]) -> Dict[str, Any]:
    images = ar.get("images") or []
    photo = images[0]["url"] if images else None
    return {
        "spotify_id": ar.get("id"),
        "name": ar.get("name"),
        "genres": ar.get("genres") or [],
        "photo_url": photo,
        "external_url": (ar.get("external_urls") or {}).get("spotify"),
    }


def _map_track_item(t: Dict[str, Any]) -> Dict[str, Any]:
    album = t.get("album") or {}
    images = album.get("images") or []
    cover = images[0]["url"] if images else None
    primary_artist = (t.get("artists") or [{}])[0]
    return {
        "spotify_id": t.get("id"),
        "title": t.get("name"),
        "duration_ms": t.get("duration_ms"),
        "track_number": t.get("track_number"),
        "album": {
            "spotify_id": album.get("id"),
            "title": album.get("name"),
            "release_date": album.get("release_date"),
            "cover_url": cover,
        },
        "artist_name": primary_artist.get("name"),
        "artist_spotify_id": primary_artist.get("id"),
        "external_url": (t.get("external_urls") or {}).get("spotify"),
    }