from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.repositories.album_repo import AlbumRepository
from app.repositories.artist_repo import ArtistRepository
from app.repositories.track_repo import TrackRepository
from app.domain.schemas import AlbumDetail, AlbumOut, ArtistOut, TrackOut
from app.utils.mapping import normalize_release_date
from app.clients.spotify_client import spotify
from app.core.singleflight import single_flight


class AlbumService:
    def __init__(self, db: Session):
        self.db = db
        self.albums = AlbumRepository(db)
        self.artists = ArtistRepository(db)
        self.tracks = TrackRepository(db, self.artists)

    def get_album_detail(self, album_id: str) -> AlbumDetail:
        al = self.albums.get_by_id(album_id)
        if not al:
            raise HTTPException(status_code=404, detail="album not found in DB")
        # NOTE: join to artists omitted in skeleton
        artists = []
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
            artists=[ArtistOut(id=str(a.id), name=a.name, spotify_id=a.spotify_id) for a in artists],
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

    def sync_album_by_spotify(self, spotify_album_id: str, market: str | None) -> AlbumDetail:
        key = f"sync:{spotify_album_id}"
        single_flight.acquire(key)
        try:
            # 1) 앨범 본문
            a = spotify.get_album(spotify_album_id, market=market)
            title = a.get("name")
            rdate = normalize_release_date(a.get("release_date"), a.get("release_date_precision"))
            cover = (a.get("images") or [{}])[0].get("url")
            album_type = a.get("album_type")
            ext_refs = {"spotify_url": (a.get("external_urls") or {}).get("spotify")}

            # 2) 앨범 아티스트 upsert + 링크용 로컬 ID 수집
            local_artist_ids = []
            for art in a.get("artists", []):
                ent = self.artists.upsert_min(
                    spotify_id=art["id"],
                    name=art.get("name") or "",
                    ext_refs={"spotify_url": (art.get("external_urls") or {}).get("spotify")},
                )
                self.db.flush()
                local_artist_ids.append(ent.id)

            # 3) 앨범 upsert
            local_album = self.albums.upsert_album_min(
                spotify_id=a["id"],
                title=title,
                release_date=rdate,
                cover_url=cover,
                album_type=album_type,
                ext_refs=ext_refs,
            )
            self.db.flush()

            # 4) 앨범-아티스트 링크
            if local_artist_ids:
                self.albums.link_album_artists(local_album.id, local_artist_ids)

            # 5) 트랙 전체 수신
            tracks = spotify.get_album_tracks_all(spotify_album_id, market=market) or []

            # 6) 트랙의 모든 아티스트 선행 upsert (피처링 포함)
            seen = set()
            for t in tracks:
                for art in (t.get("artists") or []):
                    sid = art.get("id")
                    if not sid or sid in seen:
                        continue
                    seen.add(sid)
                    self.artists.upsert_min(
                        spotify_id=sid,
                        name=art.get("name") or "",
                        ext_refs={"spotify_url": (art.get("external_urls") or {}).get("spotify")},
                    )
            self.db.flush()

            # 7) 트랙 upsert + (DB-only) 트랙-아티스트 링크
            self.tracks.upsert_tracks_with_artists_db_only(
                album_local_id=local_album.id,
                tracks_json=tracks,
            )

            self.db.commit()

            # 응답 구성
            full_tracks = self.tracks.get_by_album(local_album.id)

            resp_artists: list[ArtistOut] = []
            for aid in local_artist_ids:
                a_ent = self.artists.get_by_id(aid)
                if a_ent:
                    resp_artists.append(
                        ArtistOut(id=str(a_ent.id), name=a_ent.name, spotify_id=a_ent.spotify_id)
                    )

            return AlbumDetail(
                album=AlbumOut(
                    id=str(local_album.id),
                    title=local_album.title,
                    release_date=local_album.release_date.isoformat()
                    if local_album.release_date
                    else None,
                    cover_url=local_album.cover_url,
                    album_type=local_album.album_type,
                    spotify_id=local_album.spotify_id,
                ),
                artists=resp_artists,
                tracks=[
                    TrackOut(
                        id=str(t.id),
                        title=t.title,
                        track_no=t.track_no,
                        duration_sec=t.duration_sec,
                        spotify_id=t.spotify_id,
                    )
                    for t in full_tracks
                ],
                meta={"source": "spotify+db"},
            )
        except Exception:
            self.db.rollback()
            raise
        finally:
            single_flight.release(key)