"""FEAT-release-calendar Step 6 — read repo over artist_release_events.

Raw text() SQL on purpose: this repo's shared_db pin (v0.26.0) predates the V44
tables (`artist_release_events` / `artist_source_ids`), and the Step-4 worker
poller set the precedent of querying them via text() without a pin bump
(lastfm_sync_service precedent). artist_repo.py already uses text() here, so
this stays inside existing repo conventions. DB-only — no external call.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Public release-calendar discovery is intentionally bounded to the popularity
# watchlist accepted in FEAT-release-calendar OQ1. Personal tracking widens the
# worker's shared observation table, but must not make a member's tracked-only
# announcements observable on the public calendar. Confirmed releases remain
# public even if the artist later falls below the watchlist floor.
PUBLIC_RELEASE_POP_MIN = 50

_SELECT_EVENTS = text(
    """
    SELECT re.artist_id,
           re.source,
           re.title,
           re.release_type,
           re.release_date,
           re.status,
           re.spotify_album_id,
           a.name        AS artist_name,
           a.popularity  AS artist_popularity,
           al.label      AS album_label,
           (SELECT count(*) FROM album_artists aa WHERE aa.album_id = al.id)
                         AS album_n_artists
      FROM artist_release_events re
      JOIN artists a ON a.id = re.artist_id
      -- DATA-release-noise Step 1: confirmed rows carry spotify_album_id, so join
      -- the catalog album to surface label + credited-artist count for the read-
      -- side compilation filter. Pre-confirm (announced) rows have no album yet →
      -- LEFT JOIN leaves album_label NULL / album_n_artists 0 (title signal only).
      LEFT JOIN albums al ON al.spotify_id = re.spotify_album_id
     WHERE re.release_date >= :date_from
       AND re.release_date <= :date_to
       AND (a.popularity >= :public_pop_min OR re.status = 'released')
     ORDER BY re.release_date, a.name, re.source
    """
)


class ReleaseEventRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_events(self, *, date_from: date, date_to: date) -> List[Dict[str, Any]]:
        """One dict per (source, source_key) observation row inside the window
        (inclusive both ends), joined with artist name/popularity. Materialized
        to plain dicts so the service layer is unit-testable without a DB."""
        rows = self.db.execute(
            _SELECT_EVENTS,
            {
                "date_from": date_from,
                "date_to": date_to,
                "public_pop_min": PUBLIC_RELEASE_POP_MIN,
            },
        ).mappings()
        return [dict(r) for r in rows]
