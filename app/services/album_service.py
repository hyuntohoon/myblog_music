from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.repositories.album_repo import AlbumRepository
from app.repositories.artist_repo import ArtistRepository
from app.repositories.track_repo import TrackRepository
from app.domain.schemas import AlbumDetail, AlbumOut, ArtistOut, TrackOut
from app.utils.mapping import normalize_release_date
from app.clients.spotify_client import spotify
from app.core.singleflight import single_flight
from typing import List, Dict, Tuple, Optional


class AlbumService:
    def __init__(self, db: Session):
        self.db = db
        self.albums = AlbumRepository(db)
        self.artists = ArtistRepository(db)
        self.tracks = TrackRepository(db, self.artists)

    def get_primary_artist_map(self, album_ids: List[str]) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
            """
            주어진 album_ids에 대해 (album_id -> (artist_name, artist_spotify_id)) 맵 반환.
            여러 아티스트가 있어도 최초 한 명만 대표로 사용.
            """
            if not album_ids:
                return {}

            stmt = (
                select(AlbumArtist.album_id, Artist.name, Artist.spotify_id)
                .join(Artist, Artist.id == AlbumArtist.artist_id)
                .where(AlbumArtist.album_id.in_(album_ids))
                # 필요하면 여기서 정렬 기준 추가 (예: 주 아티스트 우선)
            )
            rows = self.db.execute(stmt).all()

            out: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
            for aid, name, spid in rows:
                # 앨범 하나에 여러 행이 있어도 맨 처음 것만 대표로
                if aid not in out:
                    out[str(aid)] = (name, spid)
            return out

    def get_album_detail(self, album_id: str) -> AlbumDetail:
    # 앨범 + 아티스트를 한 번에 조회
        al, artists = self.albums.get_with_artists(album_id)
        if not al:
            raise HTTPException(status_code=404, detail="album not found in DB")

        tracks = self.tracks.get_by_album(al.id)

        return AlbumDetail(
            album=AlbumOut(
                id=str(al.id),
                title=al.title,
                release_date=al.release_date.isoformat() if al.release_date else None,
                cover_url=al.cover_url,
                album_type=al.album_type,
                spotify_id=al.spotify_id,
            ),
            artists=[
                ArtistOut(id=str(a.id), name=a.name, spotify_id=a.spotify_id)
                for a in artists
            ],
            tracks=[
                TrackOut(
                    id=str(t.id),
                    title=t.title,
                    track_no=t.track_no,
                    duration_sec=t.duration_sec,
                    spotify_id=t.spotify_id,
                )
                for t in tracks
            ],
            meta={"source": "db"},
        )
    

    def get_album_detail_by_spotify(self, spotify_id: str) -> AlbumDetail:
        # albums 리포지토리에 이 메서드가 있다고 가정 (없으면 하나 만들면 됨)
        al = self.albums.get_by_spotify_id(spotify_id)
        if not al:
            raise HTTPException(status_code=404, detail="album not found in DB")

        # 내부 UUID로 기존 로직 재사용
        return self.get_album_detail(str(al.id))