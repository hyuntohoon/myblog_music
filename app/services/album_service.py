from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.repositories.album_repo import AlbumRepository
from app.repositories.artist_repo import ArtistRepository
from app.repositories.track_repo import TrackRepository
from app.domain.schemas import AlbumDetail, AlbumOut, ArtistOut, TrackOut


class AlbumService:
    def __init__(self, db: Session):
        self.db = db
        self.albums = AlbumRepository(db)
        self.artists = ArtistRepository(db)
        self.tracks = TrackRepository(db, self.artists)

    def get_album_detail(self, album_id: str) -> AlbumDetail:
        al, artists = self.albums.get_with_artists(album_id)
        if not al:
            raise HTTPException(status_code=404, detail="album not found in DB")

        tracks = self.tracks.get_by_album(al.id)
        album_artist_ids = {a.id for a in artists}

        return AlbumDetail(
            album=AlbumOut(
                id=str(al.id),
                title=al.title,
                release_date=al.release_date.isoformat() if al.release_date else None,
                cover_url=al.cover_url,
                album_type=al.album_type,
                spotify_id=al.spotify_id,
                external_url=(al.ext_refs or {}).get("spotify_url"),
                label=al.label,
                best_new=bool(getattr(al, "best_new", False)),
            ),
            artists=[
                ArtistOut(
                    id=str(a.id),
                    name=a.name,
                    spotify_id=a.spotify_id,
                    photo_url=a.photo_url,
                    genres=a.genres or [],
                    followers_count=a.followers,
                    popularity=a.popularity,
                    spotify_url=a.spotify_url,
                )
                for a in artists
            ],
            tracks=[
                TrackOut(
                    id=str(t.id),
                    title=t.title,
                    track_no=t.track_no,
                    duration_sec=t.duration_sec,
                    spotify_id=t.spotify_id,
                    feat_artist_names=sorted(
                        ta.name for ta in (t.artists or [])
                        if ta.id not in album_artist_ids and ta.name
                    ),
                )
                for t in tracks
            ],
            meta={"source": "db"},
        )
    

    def get_album_detail_by_spotify(self, spotify_id: str) -> AlbumDetail:
        al = self.albums.get_by_spotify_id(spotify_id)
        if not al:
            raise HTTPException(status_code=404, detail="album not found in DB")

        # 내부 UUID로 기존 로직 재사용
        return self.get_album_detail(str(al.id))