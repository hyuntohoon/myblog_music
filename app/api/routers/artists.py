from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.schemas import ArtistHero, SearchResult, TrackItem
from app.repositories.album_repo import AlbumRepository
from app.services.artist_service import ArtistService

router = APIRouter()


def _service(db: Session) -> ArtistService:
    return ArtistService(db, AlbumRepository(db))


@router.get("/{artist_id}/albums", response_model=SearchResult)
def get_artist_albums(
    artist_id: str = Path(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return _service(db).list_albums_by_artist(artist_id=artist_id, limit=limit, offset=offset)


# FEAT-writer-lowfreq-redesign Step 3 — three new endpoints for the writer's
# artist drill-in panel. All DB-only (no synchronous Spotify call per
# CLAUDE.md rule #9). 404 paths feed the frontend's poll/fallback flow.

@router.get("/by-spotify/{spotify_id}", response_model=ArtistHero)
def get_artist_by_spotify(
    spotify_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
):
    # Route order matters — declared before the parametric `/{artist_id}`
    # below so FastAPI matches the literal segment first.
    hero = _service(db).get_hero_by_spotify_id(spotify_id)
    if not hero:
        # Absorb-tracking table doesn't exist today, so we cannot distinguish
        # "truly unknown" from "pending." Frontend polls until ready or gives up.
        raise HTTPException(status_code=404, detail="artist not found")
    return hero


@router.get("/{artist_id}/top-tracks", response_model=List[TrackItem])
def get_artist_top_tracks(
    artist_id: str = Path(...),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return _service(db).list_top_tracks(artist_id=artist_id, limit=limit)


@router.get("/{artist_id}", response_model=ArtistHero)
def get_artist(
    artist_id: str = Path(...),
    db: Session = Depends(get_db),
):
    hero = _service(db).get_hero_by_id(artist_id)
    if not hero:
        raise HTTPException(status_code=404, detail="artist not found")
    return hero