from __future__ import annotations

from typing import Set, Tuple

from cachetools import TTLCache
from sqlalchemy.orm import Session

from app.repositories.artist_repo import ArtistRepository
from app.repositories.album_repo import AlbumRepository
from app.repositories.track_repo import TrackRepository

from app.domain.schemas import ExplainEntry, UnifiedSearchResult

from app.mappers.album_mapper import AlbumItemMapper
from app.mappers.artist_mapper import ArtistItemMapper
from app.mappers.track_mapper import TrackItemMapper

ALLOWED_TYPES: Set[str] = {"album", "artist", "track"}

# FEAT-music-edge-cache Step 5 — per-process unified-search result cache.
# DB-protection on the CDN/browser cache-miss path: a warm Lambda container reuses
# a recent identical search instead of re-hitting Neon. Bounded + short TTL; the
# staleness budget is minutes (owner-accepted), so no active invalidation. Not
# shared across containers; no lock needed — a Lambda container handles one event
# at a time. Caches the immutable UnifiedSearchResult (never mutated downstream).
_UNIFIED_TTL_SEC = 60
_UNIFIED_CACHE_MAXSIZE = 256
_unified_cache: TTLCache = TTLCache(maxsize=_UNIFIED_CACHE_MAXSIZE, ttl=_UNIFIED_TTL_SEC)

# Path labels for the merge/dedup phase. Ranking precedence:
#   decomposed (most precise multi-token read) > literal > expansion.
PATH_DECOMPOSED = "decomposed"
PATH_LITERAL = "literal"
PATH_EXPANSION = "expansion"

# Per-artist cap on the artist→tracks expansion query (BUG-19 Q2).
ARTIST_TRACKS_EXPANSION_CAP = 50
# Per-artist cap on the artist→albums expansion query (mirrors track cap for symmetry).
ARTIST_ALBUMS_EXPANSION_CAP = 50

# Step 6 (A2) — structured multi-token decomposition bounds. Only queries with
# 2–3 whitespace tokens are decomposed; each contributes a small, bounded set of
# (artist_part, title_part) splits (clamped to DECOMP_MAX_SPLITS), and only the
# top DECOMP_ARTIST_CANDIDATES artists per split feed the intersection.
DECOMP_MIN_TOKENS = 2
DECOMP_MAX_TOKENS = 3
DECOMP_MAX_SPLITS = 6
DECOMP_ARTIST_CANDIDATES = 5


def _decomposition_splits(tokens: list[str]) -> list[tuple[str, str]]:
    """Contiguous (artist_part, title_part) split candidates + their reverses.

    For ``["A", "B", "C"]`` → ``("A","B C"), ("B C","A"), ("A B","C"), ("C","A B")``
    — every contiguous prefix/suffix cut, taken both ways so the artist token can
    sit on either side of the title. Deduped, clamped to DECOMP_MAX_SPLITS
    (2 tokens → 2 splits, 3 tokens → 4 splits).
    """
    splits: list[tuple[str, str]] = []
    seen: Set[tuple[str, str]] = set()
    for k in range(1, len(tokens)):
        left = " ".join(tokens[:k])
        right = " ".join(tokens[k:])
        for pair in ((left, right), (right, left)):
            if pair not in seen:
                seen.add(pair)
                splits.append(pair)
    return splits[:DECOMP_MAX_SPLITS]


def _similarity(name: str | None, q: str) -> int:
    """Cheap literal-similarity score used to rank within the literal-path slice.

    3 = exact (case-insensitive), 2 = startswith, 1 = contains, 0 = none.
    Computed in Python on the already-trimmed literal result set rather than
    pushed into SQL — keeps the search_by_* queries simple and the set is small.
    """
    if not name:
        return 0
    n = name.lower()
    qq = q.lower()
    if n == qq:
        return 3
    if n.startswith(qq):
        return 2
    if qq in n:
        return 1
    return 0


class SearchService:
    def __init__(self, db: Session):
        self.db = db
        self.artist_repo = ArtistRepository(db)
        self.album_repo = AlbumRepository(db)
        self.track_repo = TrackRepository(db, self.artist_repo)

    def unified_search(
        self,
        *,
        q: str,
        limit: int,
        offset: int,
        types: Set[str] | None = None,
        artist_offset: int | None = None,
        album_offset: int | None = None,
        track_offset: int | None = None,
        explain: bool = False,
    ) -> UnifiedSearchResult:
        """Cache-fronted entry point (FEAT-music-edge-cache Step 5).

        Returns a recent identical result from the per-process TTL cache when the
        container is warm; otherwise computes and stores it. The key is the
        resolved argument tuple (so ``types=None`` and ``types=ALLOWED_TYPES``
        collapse to one entry). The DB session is intentionally NOT in the key —
        a cached result is a DB-state snapshot bounded by the TTL.
        """
        wanted = types if types is not None else ALLOWED_TYPES
        key = (
            q,
            tuple(sorted(wanted)),
            limit,
            offset,
            artist_offset,
            album_offset,
            track_offset,
            explain,
        )
        hit = _unified_cache.get(key)
        if hit is not None:
            return hit
        result = self._compute_unified_search(
            q=q,
            limit=limit,
            offset=offset,
            types=types,
            artist_offset=artist_offset,
            album_offset=album_offset,
            track_offset=track_offset,
            explain=explain,
        )
        _unified_cache[key] = result
        return result

    def _compute_unified_search(
        self,
        *,
        q: str,
        limit: int,
        offset: int,
        types: Set[str] | None = None,
        artist_offset: int | None = None,
        album_offset: int | None = None,
        track_offset: int | None = None,
        explain: bool = False,
    ) -> UnifiedSearchResult:
        """BUG-19: literal match → 1-hop expansion → cross-bucket dedup →
        path-dependent ranking → per-bucket trim. See `docs/rfcs/BUG-19-*`.
        """
        wanted = types if types is not None else ALLOWED_TYPES

        # Resolve per-bucket offsets — explicit override wins, else fall back to
        # the singular `offset` (Q3 (a) additive shape, backward-compatible).
        a_off = artist_offset if artist_offset is not None else offset
        al_off = album_offset if album_offset is not None else offset
        t_off = track_offset if track_offset is not None else offset

        # ---- Phase 1: literal match per requested bucket ----
        literal_artists = (
            self.artist_repo.search_by_name(q, limit, a_off) if "artist" in wanted else []
        )
        literal_albums = (
            self.album_repo.search_by_title(q, limit, al_off) if "album" in wanted else []
        )
        literal_tracks = (
            self.track_repo.search_by_title(q, limit, t_off) if "track" in wanted else []
        )

        # ---- Phase 1.5: structured multi-token decomposition (Step 6 / A2) ----
        # Parse the query once here (single boundary). For a 2–3 token query,
        # split it into (artist_part, title_part) and intersect title-token
        # album/track matches with albums/tracks credited to an artist matching
        # artist_part. This is a higher-precision read of "<artist> <title>"
        # queries than the whole-string fuzzy match, which dilutes similarity
        # with the artist token. Decomposed rows rank above literal/expansion.
        decomp_albums, decomp_album_sim = self._decompose(
            q, "album", limit
        ) if "album" in wanted else ([], {})
        decomp_tracks, decomp_track_sim = self._decompose(
            q, "track", limit
        ) if "track" in wanted else ([], {})

        # ---- Phase 2: 1-hop expansion (strictly 1, no transitive walks) ----
        exp_artists: list = []
        exp_albums: list = []
        exp_tracks: list = []

        # artist match → that artist's albums + tracks
        if "album" in wanted:
            for ar in literal_artists:
                exp_albums.extend(
                    self.album_repo.list_by_artist_id_simple(
                        ar.id, limit=ARTIST_ALBUMS_EXPANSION_CAP
                    )
                )
        if "track" in wanted:
            for ar in literal_artists:
                exp_tracks.extend(
                    self.track_repo.list_by_artist_id(
                        ar.id, limit=ARTIST_TRACKS_EXPANSION_CAP
                    )
                )

        # album match → that album's tracks + artists
        if "track" in wanted and literal_albums:
            exp_tracks.extend(
                self.track_repo.list_by_album_ids([al.id for al in literal_albums])
            )
        if "artist" in wanted:
            for al in literal_albums:
                # eager-loaded by album_repo.search_by_title — no query here
                exp_artists.extend(al.artists or [])

        # track match → that track's album + artists
        if "album" in wanted:
            for t in literal_tracks:
                if t.album is not None:
                    exp_albums.append(t.album)
        if "artist" in wanted:
            for t in literal_tracks:
                exp_artists.extend(t.artists or [])

        # ---- Phase 3: merge & dedup, retaining the strongest path ----
        # Group order = path precedence: first occurrence of an id wins, so a row
        # reached via decomposition keeps that label even if it also matched
        # literally or via expansion.
        artists_merged, artist_path = _merge_paths(
            (literal_artists, PATH_LITERAL), (exp_artists, PATH_EXPANSION)
        )
        albums_merged, album_path = _merge_paths(
            (decomp_albums, PATH_DECOMPOSED),
            (literal_albums, PATH_LITERAL),
            (exp_albums, PATH_EXPANSION),
        )
        tracks_merged, track_path = _merge_paths(
            (decomp_tracks, PATH_DECOMPOSED),
            (literal_tracks, PATH_LITERAL),
            (exp_tracks, PATH_EXPANSION),
        )

        # ---- Phase 4: rank per bucket per the path-dependent rules ----
        ranked_artists = _rank_artists(artists_merged, artist_path, q)
        ranked_albums = _rank_albums(albums_merged, album_path, q, decomp_album_sim)
        ranked_tracks = _rank_tracks(tracks_merged, track_path, q, decomp_track_sim)

        # ---- Phase 5: trim — singular `limit` applies per bucket this step ----
        ranked_artists = ranked_artists[:limit]
        ranked_albums = ranked_albums[:limit]
        ranked_tracks = ranked_tracks[:limit]

        # primary_map covers only the final album rows actually being returned
        primary_map = self._primary_map_for(ranked_albums)

        # Step 7 (E1): per-row ranking debug, only when explicitly requested.
        debug = None
        if explain:
            debug = (
                _explain_rows("artist", ranked_artists, artist_path, q, {})
                + _explain_rows("album", ranked_albums, album_path, q, decomp_album_sim)
                + _explain_rows("track", ranked_tracks, track_path, q, decomp_track_sim)
            )

        return UnifiedSearchResult(
            artists=ArtistItemMapper.to_list(ranked_artists),
            albums=AlbumItemMapper.to_list(ranked_albums, primary_map),
            tracks=TrackItemMapper.to_list(ranked_tracks),
            debug=debug,
        )

    # ---------------- 내부 전용 ---------------- #

    def _primary_map_for(self, albums: list) -> dict[str, tuple[str | None, str | None]]:
        if not albums:
            return {}
        album_ids = [al.id for al in albums]
        return self.album_repo.get_primary_artist_map(album_ids)

    def _decompose(self, q: str, bucket: str, limit: int) -> Tuple[list, dict]:
        """Step 6 (A2): structured decomposition of a 2–3 token query.

        Returns (rows, sim_map) where rows are album/track entities reached by
        intersecting a title-token match with an artist-token match, and
        sim_map[id] is the literal similarity of the row's title against the
        *title_part* (not the whole query) — used to rank decomposed rows. For
        a 1-token query (or no split yields a hit) returns ([], {}).
        """
        tokens = q.split()
        if not (DECOMP_MIN_TOKENS <= len(tokens) <= DECOMP_MAX_TOKENS):
            return [], {}

        rows: list = []
        sim_map: dict = {}
        for artist_part, title_part in _decomposition_splits(tokens):
            artist_ids = {
                ar.id
                for ar in self.artist_repo.search_by_name(
                    artist_part, DECOMP_ARTIST_CANDIDATES, 0
                )
            }
            if not artist_ids:
                continue
            if bucket == "album":
                for al in self.album_repo.search_by_title(title_part, limit, 0):
                    if al.id in sim_map:
                        continue
                    if any(a.id in artist_ids for a in (al.artists or [])):
                        rows.append(al)
                        sim_map[al.id] = _similarity(al.title, title_part)
            else:  # track
                for t in self.track_repo.search_by_title(title_part, limit, 0):
                    if t.id in sim_map:
                        continue
                    credited = {a.id for a in (t.artists or [])}
                    credited |= {
                        a.id for a in (getattr(t.album, "artists", None) or [])
                    }
                    if credited & artist_ids:
                        rows.append(t)
                        sim_map[t.id] = _similarity(t.title, title_part)
        return rows, sim_map


def _merge_paths(*groups: Tuple[list, str]) -> Tuple[list, dict]:
    """Merge several (rows, path_label) groups keyed by `.id`, first occurrence
    wins. Groups are passed in precedence order (strongest path first), so a row
    present in an earlier group keeps that label and is not downgraded by a later
    one. Within a group, discovery order is preserved.
    """
    path: dict = {}
    seen: set = set()
    out: list = []
    for rows, label in groups:
        for row in rows:
            rid = row.id
            if rid not in seen:
                seen.add(rid)
                path[rid] = label
                out.append(row)
    return out, path


def _matched_field(bucket: str, row, q: str, path: str) -> str | None:
    """Best-effort label of *why* a row matched, for `?explain=1` triage."""
    if path == PATH_EXPANSION:
        return None  # reached via a relation, not a direct text match
    if path == PATH_DECOMPOSED:
        return "title"
    qq = q.lower()
    if bucket == "artist":
        name = (getattr(row, "name", "") or "").lower()
        if qq in name:
            return "name"
        aliases = getattr(row, "aliases", None) or []
        if any(qq in (a or "").lower() for a in aliases):
            return "alias"
        return "fuzzy"
    title = (getattr(row, "title", "") or "").lower()
    return "title" if qq in title else "fuzzy"


def _explain_rows(bucket: str, rows: list, path: dict, q: str, decomp_sim: dict) -> list:
    """Build ExplainEntry rows for one bucket, aligned to the returned order.

    `similarity` is the service-layer signal actually used to rank the row:
    the decomposition score for decomposed rows, the literal bucket score for
    literal rows, and None for expansion rows (relation-derived, not text-ranked).
    """
    out: list = []
    for i, row in enumerate(rows):
        rid = row.id
        p = path.get(rid) or PATH_EXPANSION
        text = getattr(row, "name", None) if bucket == "artist" else getattr(row, "title", None)
        if p == PATH_DECOMPOSED:
            sim = decomp_sim.get(rid)
        elif p == PATH_LITERAL:
            sim = _similarity(text, q)
        else:
            sim = None
        out.append(
            ExplainEntry(
                bucket=bucket,
                id=str(rid),
                rank=i + 1,
                path=p,
                matched_field=_matched_field(bucket, row, q, p),
                similarity=float(sim) if sim is not None else None,
                popularity=getattr(row, "popularity", None),
            )
        )
    return out


def _rank_artists(rows: list, path: dict, q: str) -> list:
    """Literal rows ordered by (similarity DESC, popularity DESC), then
    expansion-only rows by popularity DESC.
    """
    def key(a):
        is_literal = path.get(a.id) == PATH_LITERAL
        pop = getattr(a, "popularity", None) or 0
        if is_literal:
            sim = _similarity(getattr(a, "name", None), q)
            # literal slice first (group 0), high similarity + high popularity first
            return (0, -sim, -pop)
        return (1, 0, -pop)
    return sorted(rows, key=key)


def _rank_albums(rows: list, path: dict, q: str, decomp_sim: dict | None = None) -> list:
    decomp_sim = decomp_sim or {}

    def key(al):
        p = path.get(al.id)
        pop = getattr(al, "popularity", None) or 0
        if p == PATH_DECOMPOSED:
            # Top tier: similarity is against the title_part, so an exact
            # title-token match (e.g. "Proof" in "방탄소년단 Proof") scores 3.
            return (-1, -decomp_sim.get(al.id, 0), -pop)
        if p == PATH_LITERAL:
            sim = _similarity(getattr(al, "title", None), q)
            return (0, -sim, -pop)
        return (1, 0, -pop)
    return sorted(rows, key=key)


def _rank_tracks(rows: list, path: dict, q: str, decomp_sim: dict | None = None) -> list:
    """No `Track.popularity` column today — path-dependent ranking:
    - decomposed (Step 6) → similarity to the title_part (top tier)
    - literal title match → similarity to query
    - expansion (via artist or album) → Album.release_date DESC (newest first)
    """
    decomp_sim = decomp_sim or {}

    def key(t):
        p = path.get(t.id)
        if p == PATH_DECOMPOSED:
            return (-1, -decomp_sim.get(t.id, 0), 0)
        is_literal = p == PATH_LITERAL
        if is_literal:
            sim = _similarity(getattr(t, "title", None), q)
            return (0, -sim, 0)
        # Newest album first; rows with no release_date sort last within the slice.
        rd = getattr(getattr(t, "album", None), "release_date", None)
        # Sort key: smaller comes first → we want newest (largest date) first.
        # Use a tuple (has_date, neg_ordinal) so dated rows precede null-date rows.
        if rd is not None:
            return (1, 0, -rd.toordinal())
        return (1, 1, 0)
    return sorted(rows, key=key)
