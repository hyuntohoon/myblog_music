from fastapi import FastAPI
from app.api.routers import search, albums, artists

app = FastAPI(title="Music Catalog API", version="0.1.0")

app.include_router(search.router, prefix="/api/search", tags=["Search"])
app.include_router(albums.router, prefix="/api/albums", tags=["Albums"])
app.include_router(artists.router, prefix="/api/artists", tags=["Artists"])
