from __future__ import annotations

from typing import Set, Tuple
from sqlalchemy.orm import Session

from app.repositories.artist_repo import ArtistRepository
from app.repositories.album_repo import AlbumRepository
from app.repositories.track_repo import TrackRepository

from app.domain.schemas import UnifiedSearchResult

from app.mappers.album_mapper import AlbumItemMapper
from app.mappers.artist_mapper import ArtistItemMapper
from app.mappers.track_mapper import TrackItemMapper

ALLOWED_TYPES: Set[str] = {"album", "artist", "track"}

# Path labels for the merge/dedup phase. Literal beats expansion for ranking.
PATH_LITERAL = "literal"
PATH_EXPANSION = "expansion"

# Per-artist cap on the artist→tracks expansion query (BUG-19 Q2).
ARTIST_TRACKS_EXPANSION_CAP = 50
# Per-artist cap on the artist→albums expansion query (mirrors track cap for symmetry).
ARTIST_ALBUMS_EXPANSION_CAP = 50


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
        artists_merged, artist_path = _merge_by_id(literal_artists, exp_artists)
        albums_merged, album_path = _merge_by_id(literal_albums, exp_albums)
        tracks_merged, track_path = _merge_by_id(literal_tracks, exp_tracks)

        # ---- Phase 4: rank per bucket per the path-dependent rules ----
        ranked_artists = _rank_artists(artists_merged, artist_path, q)
        ranked_albums = _rank_albums(albums_merged, album_path, q)
        ranked_tracks = _rank_tracks(tracks_merged, track_path, q)

        # ---- Phase 5: trim — singular `limit` applies per bucket this step ----
        ranked_artists = ranked_artists[:limit]
        ranked_albums = ranked_albums[:limit]
        ranked_tracks = ranked_tracks[:limit]

        # primary_map covers only the final album rows actually being returned
        primary_map = self._primary_map_for(ranked_albums)

        return UnifiedSearchResult(
            artists=ArtistItemMapper.to_list(ranked_artists),
            albums=AlbumItemMapper.to_list(ranked_albums, primary_map),
            tracks=TrackItemMapper.to_list(ranked_tracks),
        )

    # ---------------- 내부 전용 ---------------- #

    def _primary_map_for(self, albums: list) -> dict[str, tuple[str | None, str | None]]:
        if not albums:
            return {}
        album_ids = [al.id for al in albums]
        return self.album_repo.get_primary_artist_map(album_ids)


def _merge_by_id(literal: list, expansion: list) -> Tuple[list, dict]:
    """Merge literal-match rows with expansion-derived rows, keyed by `.id`.

    Returns (merged_rows, path_map) where path_map[id] is PATH_LITERAL when the
    row appeared in `literal` (even if also in expansion), else PATH_EXPANSION.
    Iteration order: literal rows first (preserving query order), then
    expansion-only rows (preserving discovery order).
    """
    path: dict = {}
    seen: dict = {}
    out: list = []
    for row in literal:
        rid = row.id
        if rid not in seen:
            seen[rid] = row
            path[rid] = PATH_LITERAL
            out.append(row)
    for row in expansion:
        rid = row.id
        if rid not in seen:
            seen[rid] = row
            path[rid] = PATH_EXPANSION
            out.append(row)
        # else: row already in literal — keep literal path (don't downgrade)
    return out, path


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


def _rank_albums(rows: list, path: dict, q: str) -> list:
    def key(al):
        is_literal = path.get(al.id) == PATH_LITERAL
        pop = getattr(al, "popularity", None) or 0
        if is_literal:
            sim = _similarity(getattr(al, "title", None), q)
            return (0, -sim, -pop)
        return (1, 0, -pop)
    return sorted(rows, key=key)


def _rank_tracks(rows: list, path: dict, q: str) -> list:
    """No `Track.popularity` column today — path-dependent ranking:
    - literal title match → similarity to query
    - expansion (via artist or album) → Album.release_date DESC (newest first)
    """
    def key(t):
        is_literal = path.get(t.id) == PATH_LITERAL
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
