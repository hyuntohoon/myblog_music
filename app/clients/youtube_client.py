"""YouTube Data API v3 client (read-only, discovery only).

FEAT-youtube-playback-provider Step A2.

Scope. Two endpoints, nothing else: ``search.list`` to discover candidate
videos for a track, and ``videos.list`` to enrich those candidates with the
four fields the mapping table stores (``status.embeddable``,
``status.privacyStatus``, ``status.madeForKids``, ``contentDetails.duration``).
No OAuth, no user data — that is Milestone B and is gated on Phase 0-B.

QUOTA IS THE DESIGN CONSTRAINT, and the two calls are not comparable:
``search.list`` costs 100 units, ``videos.list`` costs 1 for up to 50 ids. The
default project pool is 10,000 units/day, i.e. ~100 discovery searches. That is
why discovery is one search per user request and never a loop, and why there is
no catalog-wide backfill anywhere in this feature (see V55's header for the
arithmetic).

Quota exhaustion is a FIRST-CLASS OUTCOME, not an error to retry. Google
signals it as HTTP 403 with ``error.errors[0].reason`` in
{quotaExceeded, dailyLimitExceeded, rateLimitExceeded, userRateLimitExceeded}.
It is raised as :class:`YouTubeQuotaExhausted` so the router can answer with a
distinct status the UI prints verbatim. Retrying inside the process would only
convert one exhausted quota into a burst of 403s.

Every outbound request carries an explicit timeout (workspace CLAUDE.md,
"Recurring bug classes").
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Google's documented reasons for "you are out of quota". Kept as a frozenset
# rather than a substring test: `rateLimitExceeded` and `quotaExceeded` are
# distinct conditions that happen to share a response shape, and a substring
# match on "quota" would silently miss `rateLimitExceeded` entirely.
QUOTA_REASONS = frozenset(
    {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}
)

# videos.list accepts at most 50 ids per call, and that cap is what makes the
# Step A5 refresh job affordable. Enforced here so no caller can quietly
# exceed it and receive a truncated response it reads as "these are gone".
VIDEOS_LIST_MAX_IDS = 50


class YouTubeError(RuntimeError):
    """Any YouTube Data API failure that is not quota exhaustion."""


class YouTubeQuotaExhausted(YouTubeError):
    """The daily quota (or a rate limit) is spent. Not retryable today."""


class YouTubeNotConfigured(YouTubeError):
    """No API key is configured. Fails closed — never falls back to unauthenticated."""


class YouTubeClient:
    """Thin, stateless wrapper. Holds no token and no session."""

    def _key(self) -> str:
        key = settings.YOUTUBE_API_KEY
        if not key:
            # Fail closed, in the same spirit as the Cognito guards: a missing
            # credential is a misconfiguration, never a reason to try anyway.
            raise YouTubeNotConfigured(
                "YOUTUBE_API_KEY is empty. Set YOUTUBE_SECRETS_PARAM to the SSM "
                "SecureString holding it (see app/core/config.py)."
            )
        return key

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = {**params, "key": self._key()}
        url = f"{settings.YOUTUBE_API_BASE}/{endpoint}"
        try:
            r = httpx.get(url, params=params, timeout=settings.YOUTUBE_HTTP_TIMEOUT)
        except httpx.HTTPError as e:
            # The key is in `params`; httpx puts the full URL in the exception
            # string, so the exception must NOT be interpolated into the message.
            logger.warning("YouTube %s transport failure: %s", endpoint, type(e).__name__)
            raise YouTubeError(f"YouTube {endpoint} request failed") from None

        if r.status_code == 403:
            reason = self._error_reason(r)
            if reason in QUOTA_REASONS:
                logger.warning("YouTube %s quota exhausted (reason=%s)", endpoint, reason)
                raise YouTubeQuotaExhausted(reason)
            logger.warning("YouTube %s forbidden (reason=%s)", endpoint, reason)
            raise YouTubeError(f"YouTube {endpoint} forbidden: {reason}")

        if r.status_code >= 400:
            logger.warning("YouTube %s HTTP %s (reason=%s)", endpoint, r.status_code, self._error_reason(r))
            raise YouTubeError(f"YouTube {endpoint} returned HTTP {r.status_code}")

        return r.json()

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

    def search_videos(self, *, q: str, max_results: int = 10) -> List[str]:
        """One `search.list` call (100 units). Returns video ids in relevance order.

        Returns ids only. Nothing from `search.list`'s own snippet is trusted for
        display: `videos.list` is authoritative and costs 1 unit for the whole
        page, so there is no reason to read the cheaper, staler copy.
        """
        body = self._get(
            "search",
            {
                "part": "id",
                "q": q,
                "type": "video",
                # Category 10 = Music. Narrows away podcast/interview uploads
                # that match on the artist name.
                "videoCategoryId": "10",
                "maxResults": max(1, min(int(max_results), 50)),
            },
        )
        out: List[str] = []
        for item in body.get("items") or []:
            vid = ((item or {}).get("id") or {}).get("videoId")
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
        body = self._get(
            "videos",
            {"part": "snippet,status,contentDetails", "id": ",".join(ids), "maxResults": VIDEOS_LIST_MAX_IDS},
        )
        return {it["id"]: it for it in (body.get("items") or []) if it.get("id")}


youtube = YouTubeClient()
