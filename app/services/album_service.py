from datetime import date
from typing import Optional

from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.core.kst import kst_today
from app.repositories.album_repo import AlbumRepository
from app.repositories.artist_repo import ArtistRepository
from app.repositories.track_repo import TrackRepository
from app.domain.schemas import (
    AlbumDetail,
    AlbumOut,
    ArtistOut,
    TrackOut,
    OnThisDayArtist,
    OnThisDayItem,
    OnThisDayResult,
)

# "오늘, 이 앨범들" — over-fetch matching rows, then dedup + limit in-memory so
# the SQL stays a plain filter. On-this-day sets are small (one month/day slice).
ON_THIS_DAY_FETCH_CAP = 200
# Service-level tunables (FEAT-home-strip-ranking Step 1).
ON_THIS_DAY_RECENCY_WEIGHT = 0.6


def _pop(obj) -> int:
    """popularity for ranking; NULL sorts last (as -1)."""
    p = getattr(obj, "popularity", None)
    return p if p is not None else -1


def _artists_primary_first(album) -> list:
    """Album artists ordered (popularity DESC, name ASC) — same deterministic
    primary pick used elsewhere (album_repo.get_primary_artist_map)."""
    arts = list(getattr(album, "artists", None) or [])
    return sorted(arts, key=lambda ar: (-_pop(ar), (ar.name or "")))


class AlbumService:
    def __init__(self, db: Session):
        self.db = db
        self.albums = AlbumRepository(db)
        self.artists = ArtistRepository(db)
        self.tracks = TrackRepository(db, self.artists)

    # FEAT-today-buckit Step 1: albums released on today's month/day in past
    # years ranked by a recency×popularity weighted score, deduped by
    # (title, primary artist) keeping the highest-popularity edition. `today`
    # injectable for tests.
    def get_on_this_day(self, *, limit: int, today: Optional[date] = None) -> OnThisDayResult:
        # KST day, not `date.today()` — the Lambda runs UTC, so between 00:00 and
        # 09:00 KST the month/day slice below was still yesterday's and readers
        # got the wrong "이 날의 앨범" (A-4 twin; see app/core/kst.py).
        today = today or kst_today()
        albums = self.albums.list_on_this_day(
            month=today.month,
            day=today.day,
            exclude_year=today.year,
            fetch_cap=ON_THIS_DAY_FETCH_CAP,
        )

        best: dict = {}
        for al in albums:
            primary = _artists_primary_first(al)
            primary_name = (primary[0].name if primary else "") or ""
            key = ((al.title or "").strip().lower(), primary_name.strip().lower())
            cur = best.get(key)
            if (
                cur is None
                or _pop(al) > _pop(cur)
                or (
                    _pop(al) == _pop(cur)
                    and al.release_date
                    and cur.release_date
                    and al.release_date > cur.release_date
                )
            ):
                best[key] = al

        def ranking_score(al) -> float:
            years_ago = max(1, today.year - al.release_date.year)
            album_popularity = al.popularity if al.popularity is not None else 0
            return ON_THIS_DAY_RECENCY_WEIGHT * (1 / years_ago) + (
                1 - ON_THIS_DAY_RECENCY_WEIGHT
            ) * (album_popularity / 100)

        deduped = sorted(
            best.values(),
            key=lambda a: (ranking_score(a), a.release_date),
            reverse=True,
        )[:limit]

        items = [
            OnThisDayItem(
                album_id=str(al.id),
                spotify_album_id=al.spotify_id,
                title=al.title,
                cover_url=al.cover_url,
                release_date=al.release_date.isoformat(),
                years_ago=today.year - al.release_date.year,
                artists=[
                    OnThisDayArtist(id=str(ar.id), name=ar.name, spotify_id=ar.spotify_id)
                    for ar in _artists_primary_first(al)
                ],
            )
            for al in deduped
        ]
        return OnThisDayResult(items=items, month=today.month, day=today.day, total=len(items))

    def get_album_detail(self, album_id: str) -> AlbumDetail:
        al, artists = self.albums.get_with_artists(album_id)
        if not al:
            raise HTTPException(status_code=404, detail="album not found in DB")

        tracks = self.tracks.get_by_album(al.id)
        album_artist_ids = {a.id for a in artists}

        return AlbumDetail(
            album=AlbumOut(
                id=str(al.id),
                title=al.title,
                release_date=al.release_date.isoformat() if al.release_date else None,
                cover_url=al.cover_url,
                album_type=al.album_type,
                spotify_id=al.spotify_id,
                external_url=(al.ext_refs or {}).get("spotify_url"),
                label=al.label,
                best_new=bool(getattr(al, "best_new", False)),
            ),
            artists=[
                ArtistOut(
                    id=str(a.id),
                    name=a.name,
                    spotify_id=a.spotify_id,
                    photo_url=a.photo_url,
                    genres=a.genres or [],
                    followers_count=a.followers,
                    popularity=a.popularity,
                    spotify_url=a.spotify_url,
                )
                for a in artists
            ],
            tracks=[
                TrackOut(
                    id=str(t.id),
                    title=t.title,
                    track_no=t.track_no,
                    duration_sec=t.duration_sec,
                    spotify_id=t.spotify_id,
                    feat_artist_names=sorted(
                        ta.name for ta in (t.artists or [])
                        if ta.id not in album_artist_ids and ta.name
                    ),
                )
                for t in tracks
            ],
            meta={"source": "db"},
        )
    

    def get_album_detail_by_spotify(self, spotify_id: str) -> AlbumDetail:
        al = self.albums.get_by_spotify_id(spotify_id)
        if not al:
            raise HTTPException(status_code=404, detail="album not found in DB")

        # 내부 UUID로 기존 로직 재사용
        return self.get_album_detail(str(al.id))
