from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
import os
import uuid
import json as _json
import boto3

from app.core.db import get_db
from app.services.search_service import SearchService
from app.domain.schemas import SearchResult
from app.clients.spotify_client import spotify

router = APIRouter()

# =========================
# SQS 설정 (LocalStack/.env에 맞춘 최소 구성)
# =========================
AWS_REGION    = os.getenv("AWS_DEFAULT_REGION", "ap-northeast-2")
SQS_ENDPOINT  = os.getenv("LOCALSTACK_ENDPOINT", "http://localhost:4566").rstrip("/")
QUEUE_NAME    = os.getenv("QUEUE_NAME", "test-queue")
ACCOUNT_ID    = os.getenv("AWS_ACCOUNT_ID", "000000000000")

# FIFO 여부는 큐 이름으로 자동 판별
SQS_IS_FIFO   = QUEUE_NAME.endswith(".fifo")

# 보편적인 LocalStack path 전략 URL 형식으로 기본값 구성
# (테스트/로컬에서 큐가 미리 생성되어 있다는 전제. 필요시 SQS_QUEUE_URL 을 .env 로 직접 주입 가능)
SQS_QUEUE_URL = os.getenv(
    "SQS_QUEUE_URL",
    f"{SQS_ENDPOINT}/{ACCOUNT_ID}/{QUEUE_NAME}",
).strip()

# boto3 클라이언트 (자격증명은 .env의 AWS_ACCESS_KEY_ID/SECRET 을 그대로 사용)
sqs = boto3.client(
    "sqs",
    region_name=os.getenv("AWS_DEFAULT_REGION", "ap-northeast-2"),
    endpoint_url=os.getenv("LOCALSTACK_ENDPOINT", "http://localhost:4566"),
)

# 허용 타입
ALLOWED_TYPES = {"album", "artist", "track"}

# =========================
# 진단 엔드포인트
# =========================
@router.get("/_diag/sqs")
def _diag_sqs():
    info = {"region": AWS_REGION, "endpoint": SQS_ENDPOINT, "queue": SQS_QUEUE_URL, "fifo": SQS_IS_FIFO}
    try:
        sqs.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody='{"ping":"from-app"}',
            **({"MessageGroupId": "album-sync", "MessageDeduplicationId": "diag"} if SQS_IS_FIFO else {}),
        )
        return {"ok": True, "info": info}
    except Exception as e:
        return {"ok": False, "err": str(e), "info": info}

# =========================
# 1) 기본 검색 (DB 우선)
# =========================
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
    svc = SearchService(db)
    return svc.basic_search(mode=mode, q=q, limit=limit, offset=offset)

# =========================
# 2) 후보 검색 (+ 앨범 동기화 enqueue)
# =========================
@router.get("/candidates", summary="Spotify 후보 검색(읽기 전용 + 앨범 동기화 enqueue)")
def search_candidates(
    q: str = Query(..., description="Spotify 검색 쿼리"),
    type: str = Query("album,artist,track", description='허용: "album,artist,track" 중 조합'),
    market: Optional[str] = Query(None, description="예: KR, US"),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0, le=1000),
    include_external: Optional[str] = Query(None, description='선택값: "audio"'),
    db: Session = Depends(get_db),
):
    if include_external not in (None, "audio"):
        raise HTTPException(status_code=400, detail='include_external must be "audio" or omitted')

    raw_types = [t.strip().lower() for t in type.split(",") if t.strip()]
    wanted = [t for t in raw_types if t in ALLOWED_TYPES]
    if not wanted:
        raise HTTPException(status_code=400, detail=f"type must include at least one of {sorted(ALLOWED_TYPES)}")

    type_str = ",".join(wanted)
    data = spotify.search(
        q=q, type=type_str, market=market, limit=limit, offset=offset, include_external=include_external
    )

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

    # enqueue (디버그 포함)
    debug = {"enqueued": 0, "album_ids": None, "last_error": None}
    try:
        if SQS_QUEUE_URL:
            album_ids = _collect_album_ids_for_sync(out)
            debug["album_ids"] = album_ids
            if album_ids:
                _enqueue_album_sync(album_ids, market or os.getenv("DEFAULT_MARKET", "KR"))
                debug["enqueued"] = len(album_ids)
    except Exception as e:
        debug["last_error"] = str(e)
        if os.getenv("SYNC_TEST_STRICT"):
            raise

    out["_debug"] = debug
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

# ===== 앨범 ID 수집 & SQS enqueue =====
def _collect_album_ids_for_sync(out: Dict[str, Any]) -> List[str]:
    ids = set()
    for a in out.get("albums") or []:
        sid = (a or {}).get("spotify_id")
        if sid:
            ids.add(sid)
    for t in out.get("tracks") or []:
        alb = (t or {}).get("album") or {}
        sid = alb.get("spotify_id") or alb.get("id")
        if sid:
            ids.add(sid)
    return list(ids)

def _enqueue_album_sync(album_ids: List[str], market: str) -> None:
    if not album_ids:
        return
    BATCH = 10
    for i in range(0, len(album_ids), BATCH):
        chunk = album_ids[i : i + BATCH]
        entries: List[Dict[str, Any]] = []
        for sid in chunk:
            body = {"spotify_album_id": sid, "market": market}
            entry: Dict[str, Any] = {
                "Id": str(uuid.uuid4()),
                "MessageBody": _json.dumps(body, separators=(",", ":"), ensure_ascii=False),
            }
            if SQS_IS_FIFO:
                entry["MessageGroupId"] = "album-sync"
                entry["MessageDeduplicationId"] = f"{sid}:{market}"
            entries.append(entry)
        sqs.send_message_batch(QueueUrl=SQS_QUEUE_URL, Entries=entries)