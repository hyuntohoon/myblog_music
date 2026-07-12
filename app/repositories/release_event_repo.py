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
           a.popularity  AS artist_popularity
      FROM artist_release_events re
      JOIN artists a ON a.id = re.artist_id
     WHERE re.release_date >= :date_from
       AND re.release_date <= :date_to
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
            _SELECT_EVENTS, {"date_from": date_from, "date_to": date_to}
        ).mappings()
        return [dict(r) for r in rows]
