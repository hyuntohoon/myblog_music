"""YouTube Data API v3 client (read-only, discovery only).

FEAT-youtube-playback-provider Step A2.

Scope. Two endpoints, nothing else: ``search.list`` to discover candidate
videos for a track, and ``videos.list`` to enrich those candidates with the
four fields the mapping table stores (``status.embeddable``,
``status.privacyStatus``, ``status.madeForKids``, ``contentDetails.duration``).
No OAuth, no user data — that is Milestone B and is gated on Phase 0-B.

QUOTA IS THE DESIGN CONSTRAINT, and the two calls are not comparable:
``search.list`` costs 100 units and ``videos.list`` costs 1 for up to 50 ids,
so discovery is one search per user request and never a loop, and there is no
catalog-wide backfill anywhere in this feature (see V55's header for the
arithmetic). Nothing here counts units: the client reacts to Google's own
error ``reason``, so the exact size of the daily pool changes no code path.

THE KEY GOES IN A HEADER, NEVER IN THE QUERY STRING. Google accepts both, and
``?key=`` is the shape most of its documentation shows — but httpx logs the
full request URL at INFO on every completed request
(``httpx/_client.py``: ``logger.info('HTTP Request: %s %s ...', request.method,
request.url, ...)``), so a query-string key is one ``basicConfig(level=INFO)``
or one Lambda ``ApplicationLogLevel`` change away from being printed to
CloudWatch. The prod Lambda's root logger currently defaults to WARNING, which
is an unset default rather than a control, and this key has already had to be
treated as exposed once (RFC OQ6). ``app/clients/spotify_client.py`` puts its
credential in a header for the same reason.

Two failure conditions are kept APART because the right client behaviour
differs. ``quotaExceeded``/``dailyLimitExceeded`` is the daily pool and resets
at midnight Pacific — there is nothing to retry into today.
``rateLimitExceeded``/``userRateLimitExceeded`` is a short window that resets in
seconds, and retrying shortly is correct. Folding them together would tell a
member to come back tomorrow for a condition that clears in a moment.

Every outbound request carries an explicit timeout (workspace CLAUDE.md,
"Recurring bug classes"), and the whole two-call budget is sized to fit inside
the musicApi Lambda's 15s ceiling — see ``YOUTUBE_HTTP_TIMEOUT``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# The daily pool is spent. Resets at midnight Pacific; nothing to retry into.
DAILY_QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})
# A short rate-limit window. Clears in seconds — retrying shortly IS correct.
RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})

# videos.list accepts at most 50 ids per call, and that cap is what makes the
# Step A5 refresh job affordable. Enforced here so no caller can quietly
# exceed it and receive a truncated response it reads as "these are gone".
VIDEOS_LIST_MAX_IDS = 50


class YouTubeError(RuntimeError):
    """Any YouTube Data API failure that is not one of the cases below."""


class YouTubeQuotaExhausted(YouTubeError):
    """The DAILY quota is spent. Not retryable today."""


class YouTubeRateLimited(YouTubeError):
    """A short-window rate limit. Retryable in seconds, unlike the daily quota."""


class YouTubeNotConfigured(YouTubeError):
    """No API key is configured. Fails closed — never falls back to unauthenticated."""


class YouTubeClient:
    """Thin, stateless wrapper. Holds no token and no session."""

    def _headers(self) -> Dict[str, str]:
        key = settings.YOUTUBE_API_KEY
        if not key:
            # Fail closed, in the same spirit as the Cognito guards: a missing
            # credential is a misconfiguration, never a reason to try anyway.
            raise YouTubeNotConfigured(
                "YOUTUBE_API_KEY is empty. Set YOUTUBE_SECRETS_PARAM to the SSM "
                "SecureString holding it (see app/core/config.py)."
            )
        # Header, not `?key=` — see the module docstring.
        return {"X-goog-api-key": key}

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        headers = self._headers()
        url = f"{settings.YOUTUBE_API_BASE}/{endpoint}"
        try:
            r = httpx.get(url, params=params, headers=headers, timeout=settings.YOUTUBE_HTTP_TIMEOUT)
        except httpx.HTTPError as e:
            # `from None`: httpx puts the full request URL in its exception
            # string. The key is no longer in that URL, but the chained cause is
            # still noise on a path that has to stay boring.
            logger.warning("YouTube %s transport failure: %s", endpoint, type(e).__name__)
            raise YouTubeError(f"YouTube {endpoint} request failed") from None

        if r.status_code == 403:
            reason = self._error_reason(r)
            if reason in DAILY_QUOTA_REASONS:
                logger.warning("YouTube %s daily quota exhausted (reason=%s)", endpoint, reason)
                raise YouTubeQuotaExhausted(reason)
            if reason in RATE_LIMIT_REASONS:
                logger.warning("YouTube %s rate limited (reason=%s)", endpoint, reason)
                raise YouTubeRateLimited(reason)
            logger.warning("YouTube %s forbidden (reason=%s)", endpoint, reason)
            raise YouTubeError(f"YouTube {endpoint} forbidden: {reason}")

        if r.status_code >= 400:
            logger.warning("YouTube %s HTTP %s (reason=%s)", endpoint, r.status_code, self._error_reason(r))
            raise YouTubeError(f"YouTube {endpoint} returned HTTP {r.status_code}")

        # A 200 whose body is not the documented shape is an UPSTREAM failure,
        # not a bug here: an edge/proxy error page, or a list where an object
        # belongs, must surface as YouTubeError (-> 502) rather than as a bare
        # ValueError/AttributeError the router does not catch (-> 500).
        try:
            body = r.json()
        except Exception:
            raise YouTubeError(f"YouTube {endpoint} returned a non-JSON body") from None
        if not isinstance(body, dict):
            raise YouTubeError(f"YouTube {endpoint} returned {type(body).__name__}, expected an object")
        return body

    @staticmethod
    def _error_reason(r: httpx.Response) -> str:
        """Google's per-error `reason`, or "" when the body is not the documented shape.

        Never raises: an error path that can itself fail turns a quota answer
        into a 500.
        """
        try:
            errors = ((r.json() or {}).get("error") or {}).get("errors") or []
            return (errors[0] or {}).get("reason") or ""
        except Exception:
            return ""

    @staticmethod
    def _items(body: Dict[str, Any]) -> List[Dict[str, Any]]:
        """`items`, with every non-object entry dropped.

        `items` has been observed absent, null, and carrying nulls. Dropping
        junk here keeps both readers below symmetrical — an earlier revision
        hardened `search.list` and not `videos.list`, which is exactly how one
        of two sibling readers ends up raising an AttributeError the router
        turns into a 500.
        """
        raw = body.get("items")
        if not isinstance(raw, list):
            return []
        return [it for it in raw if isinstance(it, dict)]

    def search_videos(self, *, q: str, max_results: int = 10) -> List[str]:
        """One `search.list` call (100 units). Returns video ids in relevance order.

        Returns ids only. Nothing from `search.list`'s own snippet is trusted for
        display: `videos.list` is authoritative and costs 1 unit for the whole
        page, so there is no reason to read the cheaper, staler copy.

        NO `videoCategoryId` FILTER. Category 10 (Music) looks like an obvious
        narrowing and was measured on our own catalog before being rejected:
        it is a change to the exact input Phase 0-A scored 20/20 on, and
        official uploads are not reliably categorised as Music. Narrowing an
        input that already scores 20/20 can only lose results.
        """
        body = self._get(
            "search",
            {
                "part": "id",
                "q": q,
                "type": "video",
                "maxResults": max(1, min(int(max_results), 50)),
            },
        )
        out: List[str] = []
        for item in self._items(body):
            vid = (item.get("id") or {})
            vid = vid.get("videoId") if isinstance(vid, dict) else None
            if vid:
                out.append(vid)
        return out

    def list_videos(self, video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """One `videos.list` call (1 unit) for up to 50 ids, keyed by video id.

        An id ABSENT from the response is deleted, private, or never existed —
        the API does not report it as an error. Callers must treat "absent" as
        a state, not as a failure; that is exactly what the Step A5 job maps to
        ``verify_state='gone'``.
        """
        ids = [v for v in (video_ids or []) if v]
        if not ids:
            return {}
        if len(ids) > VIDEOS_LIST_MAX_IDS:
            raise ValueError(
                f"videos.list accepts at most {VIDEOS_LIST_MAX_IDS} ids, got {len(ids)}"
            )
        # No `maxResults`: it is not a documented parameter alongside `id`, and
        # the live API ignores it. A parameter that does nothing is noise that
        # reads like a bound.
        body = self._get("videos", {"part": "snippet,status,contentDetails", "id": ",".join(ids)})
        return {it["id"]: it for it in self._items(body) if it.get("id")}


youtube = YouTubeClient()
