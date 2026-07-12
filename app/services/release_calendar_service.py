"""FEAT-release-calendar Step 6 — calendar window read with display soft-grouping.

The store keeps one row per (source, source_key) observation (under-merge bias,
hard-key merge only — RFC design). This read path soft-groups rows for display
on (artist_id, release_date, normalized title): reversible presentation logic,
NOT a data merge. A 2-source group renders once with both sources listed
(corroboration hint); after the Step-5 release-day confirmation all matching
rows carry the same spotify_album_id and status='released', which this grouping
surfaces as a single released event.

Title normalization: no release-event normalizer exists yet anywhere in the
system (the Step-4 poller stores raw source titles), so this defines the read-
side rule from the two conventions the repos already use — feed_service's
case/whitespace-insensitive dedup key, plus the iTunes storefront " - Single" /
" - EP" title suffixes that the worker's _itunes_release_type() recognizes
(MusicBrainz titles never carry them, so stripping them lets "X - Single"
(itunes) group with "X" (musicbrainz)).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.domain.schemas import (
    ReleaseCalendarDay,
    ReleaseCalendarEvent,
    ReleaseCalendarResult,
)
from app.repositories.release_event_repo import ReleaseEventRepository

logger = logging.getLogger(__name__)

# Display-field preference inside a soft group: spotify is catalog-confirmed
# truth, musicbrainz titles are canonical (no storefront suffix), itunes last.
_SOURCE_PRIORITY = {"spotify": 0, "musicbrainz": 1, "itunes": 2}

_ITUNES_SUFFIXES = (" - single", " - ep")


def normalize_title(title: str) -> str:
    """Soft-grouping key component (NOT stored): casefold + collapse whitespace
    + strip iTunes storefront ' - Single'/' - EP' suffixes."""
    norm = " ".join((title or "").split()).casefold()
    for suffix in _ITUNES_SUFFIXES:
        if norm.endswith(suffix):
            norm = norm[: -len(suffix)].rstrip()
            break
    return norm


def _iso(d: Any) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)


class ReleaseCalendarService:
    def __init__(self, db: Session):
        self.db = db
        self.events = ReleaseEventRepository(db)

    def get_calendar(self, *, date_from: date, date_to: date) -> ReleaseCalendarResult:
        rows = self.events.list_events(date_from=date_from, date_to=date_to)

        # Soft-group on (artist_id, release_date, normalized title).
        groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            key = (
                str(row["artist_id"]),
                _iso(row["release_date"]),
                normalize_title(row["title"]),
            )
            groups.setdefault(key, []).append(row)

        events: List[ReleaseCalendarEvent] = []
        for (artist_id, iso_day, _), members in groups.items():
            members.sort(
                key=lambda r: _SOURCE_PRIORITY.get(r["source"], len(_SOURCE_PRIORITY))
            )
            preferred = members[0]
            release_type: Optional[str] = next(
                (r["release_type"] for r in members if r.get("release_type")), None
            )
            spotify_album_id: Optional[str] = next(
                (r["spotify_album_id"] for r in members if r.get("spotify_album_id")),
                None,
            )
            status = (
                "released"
                if any(r.get("status") == "released" for r in members)
                else "announced"
            )
            events.append(
                ReleaseCalendarEvent(
                    artist_id=artist_id,
                    artist_name=preferred["artist_name"],
                    artist_popularity=preferred.get("artist_popularity"),
                    title=preferred["title"],
                    release_type=release_type,
                    status=status,
                    release_date=iso_day,
                    sources=sorted({r["source"] for r in members}),
                    spotify_album_id=spotify_album_id,
                )
            )

        # Day buckets ascending; inside a day: artist popularity desc, then name.
        by_day: Dict[str, List[ReleaseCalendarEvent]] = {}
        for ev in events:
            by_day.setdefault(ev.release_date, []).append(ev)
        days = [
            ReleaseCalendarDay(
                date=iso_day,
                events=sorted(
                    by_day[iso_day],
                    key=lambda e: (-(e.artist_popularity or -1), e.artist_name, e.title),
                ),
            )
            for iso_day in sorted(by_day)
        ]

        return ReleaseCalendarResult(
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            days=days,
            total=len(events),
        )
