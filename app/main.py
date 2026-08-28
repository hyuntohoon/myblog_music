# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum  # 👈 Lambda용 어댑터
from app.api.routers import search, albums, artists, feed, releases, sync_requests
from app.core.config import settings

app = FastAPI(title="Music Catalog API", version="0.1.0")

# CORS: 실제 프론트 도메인 + (local/dev 한정) 로컬 오리진.
# FIX-bug-audit-2026-07 WS-A: localhost 오리진을 prod에서 항상 신뢰(+allow_credentials)
# 하던 것을 ENV 게이트로 좁힘 — 백엔드 CORSMiddleware와 동일한 자세.
allow_origins = ["https://www.ratemymusic.blog"]
if settings.ENV in ("local", "dev"):
    allow_origins += [
        "http://localhost:4321",
        "http://127.0.0.1:4321",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터들
app.include_router(search.router, prefix="/api/music/search", tags=["Search"])
app.include_router(sync_requests.router, prefix="/api/music/sync-requests", tags=["Sync Requests"])
app.include_router(albums.router, prefix="/api/music/albums", tags=["Albums"])
app.include_router(artists.router, prefix="/api/music/artists", tags=["Artists"])
app.include_router(feed.router, prefix="/api/music/feed", tags=["Feed"])
app.include_router(releases.router, prefix="/api/music/releases", tags=["Releases"])

# 👇 Lambda가 찾을 엔트리포인트
handler = Mangum(app)
