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

            # 2) 트랙 전체 (먼저 가져와서 아티스트 수집에 활용)
            tracks = spotify.get_album_tracks_all(spotify_album_id, market=market) or []

            # 3) 모든 아티스트 Spotify ID 수집 (앨범 + 트랙)
            album_artist_ids: List[str] = [art["id"] for art in (a.get("artists") or []) if art.get("id")]
            track_artist_ids: Set[str] = set()
            for t in tracks:
                for art in (t.get("artists") or []):
                    sid = art.get("id")
                    if sid:
                        track_artist_ids.add(sid)

            all_artist_ids: List[str] = sorted(set(album_artist_ids) | track_artist_ids)

            # 4) 배치 조회로 photo_url 맵 구성
            photo_map: Dict[str, str | None] = {}
            CHUNK = 50
            for i in range(0, len(all_artist_ids), CHUNK):
                chunk = all_artist_ids[i : i + CHUNK]
                arts = spotify.get_artists(chunk)  # [{id, name, images: [{url,...}], ...}]
                for ar in arts:
                    photo_map[ar["id"]] = (ar.get("images") or [{}])[0].get("url")

            # 5) 앨범 아티스트 upsert + 로컬 ID 수집 (photo_url 포함)
            local_artist_ids: List[str] = []
            for art in (a.get("artists") or []):
                sid = art.get("id")
                if not sid:
                    continue
                ent = self.artists.upsert_min(
                    spotify_id=sid,
                    name=art.get("name") or "",
                    photo_url=photo_map.get(sid),
                    ext_refs={"spotify_url": (art.get("external_urls") or {}).get("spotify")},
                )
                self.db.flush()
                local_artist_ids.append(ent.id)

            # 6) 앨범 upsert
            local_album = self.albums.upsert_album_min(
                spotify_id=a["id"],
                title=title,
                release_date=rdate,
                cover_url=cover,
                album_type=album_type,
                ext_refs=ext_refs,
            )
            self.db.flush()

            # 7) 앨범-아티스트 링크
            if local_artist_ids:
                self.albums.link_album_artists(local_album.id, local_artist_ids)

            # 8) 트랙의 모든 아티스트 선행 upsert (photo_url 포함)
            seen: Set[str] = set()
            for t in tracks:
                for art in (t.get("artists") or []):
                    sid = art.get("id")
                    if not sid or sid in seen:
                        continue
                    seen.add(sid)
                    self.artists.upsert_min(
                        spotify_id=sid,
                        name=art.get("name") or "",
                        photo_url=photo_map.get(sid),
                        ext_refs={"spotify_url": (art.get("external_urls") or {}).get("spotify")},
                    )
            self.db.flush()

            # 9) 트랙 upsert + 트랙-아티스트 링크(DB-only)
            self.tracks.upsert_tracks_with_artists_db_only(
                album_local_id=local_album.id,
                tracks_json=tracks,
            )

            self.db.commit()

            # 10) 응답 구성
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
        
        