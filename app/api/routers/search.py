from __future__ import annotations

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from app.core.db import get_db
from app.domain.schemas import SearchResult
from app.services.search_service import SearchService as DBSearchService  

from app.clients.sqs_client import SqsClient
from app.services.cadidate_search_service import CandidateSearchService  # (이번에 만든 후보검색 서비스)

router = APIRouter()

# -------------------------------
# 1) 기본 검색(DB 우선) - 기존 서비스 그대로 사용
# -------------------------------
@router.get("", response_model=SearchResult, summary="기본 검색(DB)")
def basic_search(
    mode: str = Query(..., description='검색 모드: "artist" 또는 "album"'),
    q: str = Query(..., min_length=1, description="검색어"),
    limit: int = Query(20, ge=1, le=100, description="페이지 크기(1~100)"),
    offset: int = Query(0, ge=0, description="오프셋(0부터)"),
    db: Session = Depends(get_db),
):
    if mode not in {"artist", "album"}:
        raise HTTPException(status_code=400, detail='mode must be "artist" or "album"')
    return DBSearchService(db).basic_search(mode=mode, q=q, limit=limit, offset=offset)


# -------------------------------
# 2) 후보 검색 (+ 앨범 동기화 enqueue) - 새 서비스로 위임
# -------------------------------
@router.get("/candidates", summary="Spotify 후보 검색(읽기 전용 + 앨범 동기화 enqueue)")
def search_candidates(
    q: str = Query(..., description="Spotify 검색 쿼리"),
    type: str = Query("album,artist,track", description='허용: "album,artist,track" 중 조합'),
    market: Optional[str] = Query(None, description="예: KR, US"),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0, le=1000),
    db: Session = Depends(get_db),
    include_external: Optional[str] = Query(None, description='선택값: "audio"'),
):
    if include_external not in (None, "audio"):
        raise HTTPException(status_code=400, detail='include_external must be "audio" or omitted')

    service = CandidateSearchService(db =db, sqs=SqsClient())
    try:
        return service.search_candidates(
            q=q, typ=type, market=market, limit=limit, offset=offset, include_external=include_external
        )
    except ValueError as ve:
        # 타입 유효성 실패 등 비즈니스 밸리데이션
        raise HTTPException(status_code=400, detail=str(ve))