"""Rank YouTube videos as candidates for one catalog track.

FEAT-youtube-playback-provider Step A2.

RANKED, NEVER AUTO-ACCEPTED. The Phase 0-A probe (20 tracks + 2 controls,
2026-09-05) is what forces this: the top result was the correct studio version
20/20, which reads like an argument for auto-accepting — but 3 of those 20 were
unofficial fan uploads that can be taken down without warning, and duration is
useless as a validator. Music videos run +5s to +17s long because of intros and
the Nirvana control ran -22s because the official video is an edit. So the delta
is surfaced to a human and never thresholded.

Candidates carry `channel_title` for the same reason: an unofficial upload is
indistinguishable from an official one by title and duration alone, and the
member is the only component that can tell them apart.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.clients.youtube_client import youtube

logger = logging.getLogger(__name__)

# ISO-8601 durations as YouTube emits them: PT#H#M#S, any part optional.
# The `T` section is optional too, because a live stream reports a bare `P0D`
# with no time part at all — measured against the live API 2026-09-06. Rejecting
# it at the regex would produce the right answer (None) for the wrong reason and
# leave the zero-guard below unreachable.
_ISO_DURATION = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


def parse_iso8601_duration(value: Optional[str]) -> Optional[int]:
    """`PT4M13S` -> 253. Returns None for anything unparseable.

    None is a real outcome, not a defect: live streams report `P0D` with no time
    part, and a candidate whose duration is unknown must still be offerable —
    it simply cannot be ranked by proximity.
    """
    if not value:
        return None
    m = _ISO_DURATION.match(value)
    if not m:
        return None
    d, h, mi, s = (int(m.group(k) or 0) for k in ("d", "h", "m", "s"))
    total = d * 86400 + h * 3600 + mi * 60 + s
    # `P0D` / `PT0S` (a live stream) parse to 0, which is not a duration. Treat
    # zero as UNKNOWN rather than as "zero seconds long" — zero would otherwise
    # win every proximity comparison and rank a 24/7 stream first.
    return total or None


class YouTubeCandidateService:
    """Read-only. Opens no DB session and no SQS client."""

    def build_query(self, *, title: str, artist_name: Optional[str]) -> str:
        """`"<artist> <title>"`, the shape the Phase 0-A probe measured.

        No field operators and no quoting: the probe scored 20/20 on the plain
        concatenation, and 7 of those matches came through YouTube's
        auto-generated "- Topic" channels, which are reached by ordinary
        relevance ranking. Quoting would be a change to a measured input.
        """
        return " ".join(p for p in ((artist_name or "").strip(), (title or "").strip()) if p)

    def find_candidates(
        self,
        *,
        title: str,
        artist_name: Optional[str],
        track_duration_sec: Optional[int],
        max_results: int,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Returns ``(query, candidates)``.

        The query is RETURNED rather than recomputed by the caller: it is what
        the member sees when nothing matched, and two call sites deriving the
        same string by convention is one edit away from a response that reports
        a query the search never ran.
        """
        q = self.build_query(title=title, artist_name=artist_name)
        if not q:
            return q, []

        video_ids = youtube.search_videos(q=q, max_results=max_results)
        if not video_ids:
            return q, []

        # One enrichment call for the whole page (1 unit). Ids absent from the
        # response are deleted/private; they are dropped rather than shown,
        # because an unplayable candidate is not a candidate.
        details = youtube.list_videos(video_ids)

        out: List[Dict[str, Any]] = []
        for rank, vid in enumerate(video_ids):
            item = details.get(vid)
            if not item:
                continue
            status = item.get("status") or {}
            snippet = item.get("snippet") or {}
            content = item.get("contentDetails") or {}

            # Only offer what can actually be embedded and played. Filtering here
            # rather than in the UI means an unpickable candidate never reaches
            # a member — and it is the same predicate the resolve read applies
            # (OQ9: embeddable IS TRUE), so the two cannot drift apart.
            if status.get("embeddable") is not True:
                continue
            if status.get("privacyStatus") != "public":
                continue

            duration_sec = parse_iso8601_duration(content.get("duration"))
            delta = (
                abs(duration_sec - track_duration_sec)
                if duration_sec is not None and track_duration_sec is not None
                else None
            )
            out.append(
                {
                    "video_id": vid,
                    "title": snippet.get("title"),
                    "channel_title": snippet.get("channelTitle"),
                    "thumbnail_url": self._thumbnail(snippet),
                    "duration_sec": duration_sec,
                    "duration_delta_sec": delta,
                    "embeddable": True,
                    "privacy_status": status.get("privacyStatus"),
                    "made_for_kids": status.get("madeForKids"),
                    "search_rank": rank,
                }
            )

        # Proximity first, YouTube's own relevance as the tiebreaker. Candidates
        # with an unknown duration sort last but are NOT dropped: "unrankable"
        # and "wrong" are different, and only the member can tell which.
        out.sort(key=lambda c: (c["duration_delta_sec"] is None, c["duration_delta_sec"] or 0, c["search_rank"]))
        return q, out

    @staticmethod
    def _thumbnail(snippet: Dict[str, Any]) -> Optional[str]:
        thumbs = snippet.get("thumbnails") or {}
        for size in ("medium", "high", "default", "standard", "maxres"):
            url = (thumbs.get(size) or {}).get("url")
            if url:
                return url
        return None
