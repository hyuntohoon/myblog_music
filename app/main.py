# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum  # 👈 Lambda용 어댑터
from app.api.routers import search, albums, artists

app = FastAPI(title="Music Catalog API", version="0.1.0")

# CORS: 로컬 + 실제 프론트 도메인
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4321",
        "http://127.0.0.1:4321",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://www.ratemymusic.blog",  # 👈 실제 프론트
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터들
app.include_router(search.router, prefix="/api/music/search", tags=["Search"])
app.include_router(albums.router, prefix="/api/music/albums", tags=["Albums"])
app.include_router(artists.router, prefix="/api/music/artists", tags=["Artists"])

# 👇 Lambda가 찾을 엔트리포인트
handler = Mangum(app)