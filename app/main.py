from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routers import search, albums, artists

app = FastAPI(title="Music Catalog API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4321", "http://127.0.0.1:4321", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router, prefix="/api/search", tags=["Search"])
app.include_router(albums.router, prefix="/api/albums", tags=["Albums"])
app.include_router(artists.router, prefix="/api/artists", tags=["Artists"])
