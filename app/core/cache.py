"""Cache-Control values for public, idempotent DB-read endpoints.

Part of FEAT-music-edge-cache. These headers turn on browser caching directly
and feed the CloudFront edge behaviors (the edge honors origin `max-age`).

Apply ONLY to 200 responses. 4xx/5xx must stay uncached — in particular the
`/by-spotify/{id}` endpoints return 404 while the worker is still absorbing an
album/artist, and the writer polls that 404 until it flips to 200
([[feedback-rfc-current-state-audit]]). Caching the pending 404 would stall that
poll, so success-only is the rule here; the edge separately sets
`error_caching_min_ttl = 0` for 404.

Staleness budget is "minutes" (owner-accepted), so detail TTL > search TTL.
"""
from __future__ import annotations

# Search results churn more (new catalog rows surface via worker sync); keep short.
SEARCH_CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=60"

# Album / artist detail is near-immutable once absorbed; cache longer.
DETAIL_CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=120"
