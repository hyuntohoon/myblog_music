# app/core/ids.py
# AUDIT-2026-07-26 A-3 (twin) — parse id strings at the route boundary.
#
# The audit named only the backend's /api/research/* routes, but the same defect
# was live here on four routes that are PUBLIC — no JWT, reachable by anyone:
#
#   500  /api/music/albums/{id}
#   500  /api/music/artists/{id}
#   500  /api/music/artists/{id}/albums
#   500  /api/music/artists/{id}/top-tracks
#
# Catalog ids are uuid columns, so a malformed value reaches psycopg and raises
# InvalidTextRepresentation. A string that is not a UUID cannot name a row, so
# "not found" is the honest answer — and it is what /albums/by-spotify already
# returned, which is why that route is untouched here (Spotify ids are not UUIDs
# and must keep taking arbitrary strings).
#
# Twin: myblog_backend `app/core/ids.py`. Separate repos, one defect class —
# a change to either belongs in the same sweep as the other.
from __future__ import annotations

import uuid

from fastapi import HTTPException


def parse_uuid_or_404(value: str, *, detail: str = "not found") -> uuid.UUID:
    """Return `value` as a UUID, or raise 404 — never let it reach the driver."""
    try:
        return uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(status_code=404, detail=detail)
