"""FEAT-release-calendar Track A — home "새 앨범" feed.

DB-only read off the daily `album_ingest` catalog: no Spotify call anywhere on
this path (hard rule #9). Over-fetch the window from the repo, then dedup by
(normalized title, primary artist) and rank by a recency×popularity weighted
score in-memory —
same orchestration shape as AlbumService.get_on_this_day.
"""
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.kst import kst_today
from app.domain.schemas import NewReleaseArtist, NewReleaseItem, NewReleasesResult
from app.repositories.album_repo import AlbumRepository
from app.services.album_service import _artists_primary_first, _pop
from app.services.compilation_filter import is_compilation_noise

# 30-day prod window holds ~80-120 album-type rows; 90-day cap fits well under this.
FEED_FETCH_CAP = 300
# Service-level tunables (FEAT-home-strip-ranking Step 1).
FEED_RECENCY_WEIGHT = 0.6
FEED_RECENCY_HORIZON_DAYS = 30


class FeedService:
    def __init__(self, db: Session):
        self.db = db
        self.albums = AlbumRepository(db)

    # `today` injectable for tests (same seam as get_on_this_day).
    def get_new_releases(
        self, *, days: int, limit: int, today: Optional[date] = None
    ) -> NewReleasesResult:
        # Same KST rule as the rest of the read path. The impact here is milder
        # than get_on_this_day — a UTC "today" only widens the rolling window by
        # a day for 9 hours — but leaving the last `date.today()` behind is how
        # this drifts back.
        today = today or kst_today()
        since = today - timedelta(days=days)
        albums = self.albums.list_new_releases(since=since, fetch_cap=FEED_FETCH_CAP)
        # Defensive re-filter with the same predicates as the repo SQL — keeps
        # the window/type behavior unit-testable without a local DB.
        albums = [
            al
            for al in albums
            if al.album_type == "album" and al.release_date and al.release_date >= since
        ]

        # DATA-release-noise Step 1: drop budget classical compilations
        # ("065 Piano Essentials" etc.) that crowd out real new releases. Read-side
        # only; the rows remain in the catalog. See compilation_filter for the
        # conservative predicate (genuine classical performances pass through).
        budget_labels = frozenset(settings.COMP_FILTER_BUDGET_LABELS)
        albums = [
            al
            for al in albums
            if not is_compilation_noise(
                title=al.title,
                label=getattr(al, "label", None),
                n_artists=len(getattr(al, "artists", None) or []),
                max_artists=settings.COMP_FILTER_MAX_ARTISTS,
                budget_labels=budget_labels,
            )
        ]

        # dedup on (normalized title, primary artist): keep the highest-popularity
        # edition, then the latest release_date (near-duplicate spotify_ids —
        # RFC "Known data caveats").
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
                    and (al.release_date or date.min) > (cur.release_date or date.min)
                )
            ):
                best[key] = al

        def ranking_score(al) -> float:
            days_since_release = (today - al.release_date).days
            album_popularity = al.popularity if al.popularity is not None else 0
            return FEED_RECENCY_WEIGHT * (
                1 - days_since_release / FEED_RECENCY_HORIZON_DAYS
            ) + (1 - FEED_RECENCY_WEIGHT) * (album_popularity / 100)

        ranked = sorted(
            best.values(),
            key=lambda a: (ranking_score(a), a.release_date),
            reverse=True,
        )[:limit]

        reviewed = self.albums.reviewed_artist_ids(
            {ar.id for al in ranked for ar in (getattr(al, "artists", None) or [])}
        )

        items = []
        for al in ranked:
            primary = _artists_primary_first(al)
            items.append(
                NewReleaseItem(
                    album_id=str(al.id),
                    spotify_album_id=al.spotify_id,
                    title=al.title,
                    cover_url=al.cover_url,
                    release_date=al.release_date.isoformat(),
                    artists=[
                        NewReleaseArtist(
                            id=str(ar.id), name=ar.name, popularity=ar.popularity
                        )
                        for ar in primary
                    ],
                    artist_popularity=(primary[0].popularity if primary else None),
                    reviewed_artist=any(ar.id in reviewed for ar in primary),
                )
            )
        return NewReleasesResult(items=items, window_days=days, total=len(items))
