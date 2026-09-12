"""DATA-release-noise (c) — read-side collapse of duplicate album editions.

Spotify hands out more than one album id for the same release (market variants,
re-uploads), and the catalog keys albums on ``spotify_id`` alone
(``worker/service/sync_service.py`` album upsert), so both ids land as separate
``albums`` rows. On 2026-09-03 prod carried **43 such groups / 43 redundant rows
of 3,947 albums (1.1%)**, and both remaining un-collapsed read paths showed them:
``/api/music/search/unified?q=iceman`` returned ICEMAN twice, and Drake's
discography listed it twice (the 2026-07-26 audit's report).

This hides the twin at *read time only*. The rows stay, so the rule is fully
reversible, no member state is touched, and — unlike a one-time delete — the
next Spotify sync cannot undo it.

**The key is stricter than the one the feed paths use, on purpose.**
``feed_service`` / ``album_service`` group on (title, primary artist) because a
feed wants one card per release; that key would also swallow a lead single into
its parent album. Here the surfaces are a *catalog* — a discography that loses
its singles is wrong — so the key adds ``total_tracks``:

    (normalized title, all artist ids, total_tracks)

Measured against prod 2026-09-03: this key collapses 43 groups and **zero**
groups whose rows differ in ``album_type`` — the 3-track "Bully" single stays
separate from the 18-track "Bully" album, "SOS Deluxe: LANA" keeps its 38-track
edition beside the two 42-track duplicates, and only 4 groups collapse rows more
than two days apart (all four genuinely one release: a 2015/2019 re-release, a
2006 placeholder date, a classical re-upload, and a single re-dated by six days).

Known residual, deliberately not solved here: the surviving row is picked from
catalog metadata, and for 2 of the 43 groups the owner's rating/bucket sits on
the *other* row (both twins are equally complete; the attached one just carries
``popularity = 0``). Reaching into member state from this service would cross the
backend↔music boundary, and re-pointing those references is the merge step the
owner did not take. Those albums stay reachable from the bucket board and the
dashboard, which link by album id.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Tuple

_WS_RE = re.compile(r"\s+")


def _norm_title(title: Any) -> str:
    return _WS_RE.sub(" ", str(title or "").strip()).casefold()


def _artist_ids(album: Any) -> Tuple[str, ...]:
    arts = getattr(album, "artists", None) or []
    return tuple(sorted(str(getattr(ar, "id", "")) for ar in arts))


def edition_key(album: Any) -> Tuple[str, Tuple[str, ...], Any]:
    """Group key for "the same release, ingested twice"."""
    return (_norm_title(getattr(album, "title", None)), _artist_ids(album),
            getattr(album, "total_tracks", None))


def _rank(album: Any) -> tuple:
    """Which twin survives: the livest catalog row wins.

    popularity DESC (NULL last) → release_date DESC (NULL last) → spotify_id ASC.
    The first two are the convention ``feed_service`` and ``album_service``
    already use; ``spotify_id`` is only a tie-break so the choice is stable
    across requests instead of following row order.
    """
    pop = getattr(album, "popularity", None)
    rel = getattr(album, "release_date", None)
    return (
        pop if pop is not None else -1,
        rel.toordinal() if rel is not None else -1,
    )


def collapse_editions(albums: Iterable[Any]) -> List[Any]:
    """Drop duplicate editions, preserving the caller's ordering.

    The survivor is chosen by :func:`_rank`, but it is emitted at the position
    of the group's *first* row, so a ranking the caller already applied (search
    relevance, discography order) is not reshuffled by this filter.
    """
    order: List[Tuple[str, Tuple[str, ...], Any]] = []
    best: dict = {}
    for al in albums:
        key = edition_key(al)
        cur = best.get(key)
        if cur is None:
            order.append(key)
            best[key] = al
            continue
        if _is_better(al, cur):
            best[key] = al
    return [best[k] for k in order]


def _is_better(candidate: Any, incumbent: Any) -> bool:
    c, i = _rank(candidate), _rank(incumbent)
    if c != i:
        return c > i
    # Same popularity and release date — fall back to the smaller spotify_id so
    # the survivor does not depend on which row the database returned first.
    return str(getattr(candidate, "spotify_id", "") or "") < str(
        getattr(incumbent, "spotify_id", "") or ""
    )
