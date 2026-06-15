# app/services/artist_service.py
from typing import Optional

from sqlalchemy.orm import Session

from app.repositories.album_repo import AlbumRepository
from app.repositories.artist_repo import ArtistRepository
from app.repositories.track_repo import TrackRepository
from app.domain.schemas import ArtistHero, ArtistIdItem, SearchResult, TrackItem
from app.mappers.album_mapper import AlbumItemMapper
from app.mappers.track_mapper import TrackItemMapper


class ArtistService:
    def __init__(
        self,
        db: Session,
        album_repo: AlbumRepository,
        artist_repo: Optional[ArtistRepository] = None,
        track_repo: Optional[TrackRepository] = None,
    ) -> None:
        self.db = db
        self.album_repo = album_repo
        # Existing callers pass only (db, album_repo); the hero/top-tracks paths
        # need the extra repos and lazily construct them when omitted.
        self.artist_repo = artist_repo or ArtistRepository(db)
        self.track_repo = track_repo or TrackRepository(db, self.artist_repo)

    def list_albums_by_artist(
        self,
        *,
        artist_id: str,
        limit: int,
        offset: int,
    ) -> SearchResult:
        albums, primary_map = self.album_repo.list_by_artistId_artist(
            artist_id=artist_id,
            limit=limit,
            offset=offset,
        )
        items = AlbumItemMapper.to_list(albums, primary_map)
        return SearchResult(type="album", items=items)

    # ----- FEAT-writer-lowfreq-redesign Step 3 -----

    def get_hero_by_id(self, artist_id: str) -> Optional[ArtistHero]:
        a = self.artist_repo.get_by_id(artist_id)
        if not a:
            return None
        return self._to_hero(a)

    def get_hero_by_spotify_id(self, spotify_id: str) -> Optional[ArtistHero]:
        # No absorb-tracking table exists yet — the by-spotify endpoint
        # collapses to "row exists → ready, else 404." Frontend polls 404 until
        # the worker writes the row. Schema keeps `status` so adding a real
        # pending shape later doesn't break the contract.
        a = self.artist_repo.get_by_spotify_id(spotify_id)
        if not a:
            return None
        return self._to_hero(a)

    def list_top_tracks(self, *, artist_id: str, limit: int) -> list[TrackItem]:
        tracks = self.track_repo.list_top_tracks_by_artist(artist_id, limit=limit)
        return TrackItemMapper.to_list(tracks)

    def list_artist_ids(self) -> list[ArtistIdItem]:
        # Catalog-artist id list for the front's build-time /artist/[id] enumeration.
        return [
            ArtistIdItem(id=i, name=n)
            for i, n in self.artist_repo.list_ids_with_albums()
        ]

    def _to_hero(self, a) -> ArtistHero:
        album_count, track_count = self.artist_repo.count_albums_and_tracks(str(a.id))
        return ArtistHero(
            id=str(a.id),
            name=a.name,
            spotify_id=a.spotify_id,
            photo_url=a.photo_url,
            genres=list(a.genres or []),
            followers=a.followers,
            popularity=a.popularity,
            spotify_url=a.spotify_url,
            album_count=album_count,
            track_count=track_count,
            status="ready",
        )