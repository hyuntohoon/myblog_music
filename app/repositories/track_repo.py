from sqlalchemy.orm import Session
from sqlalchemy import select, and_
from typing import Iterable, List, Dict, Any
from app.domain.models import Track, TrackArtist
from app.repositories.artist_repo import ArtistRepository

class TrackRepository:
    def __init__(self, db: Session, artist_repo: ArtistRepository):
        self.db = db
        self.artist_repo = artist_repo

    def get_by_album(self, album_id: str) -> List[Track]:
        return list(
            self.db.execute(select(Track).where(Track.album_id == album_id))
            .scalars()
            .all()
        )

    def upsert_tracks_with_artists_db_only(
        self,
        *,
        album_local_id: str,
        tracks_json: Iterable[dict],
        fallback_album_artists: Iterable[dict] | None = None,
    ):
        # 0) 필요한 모든 spotify_artist_id를 수집
        need_sp_ids: set[str] = set()
        tracks_list = list(tracks_json)

        for t in tracks_list:
            arts = t.get("artists") or []
            if not arts and fallback_album_artists:
                arts = list(fallback_album_artists)
            for a in arts:
                spid = a.get("id")
                if spid:
                    need_sp_ids.add(spid)

        # 1) 모두 DB에서만 로드(없으면 예외)
        sp_map = self.artist_repo.require_all_by_spotify_ids(sorted(need_sp_ids))

        # 2) 트랙 업서트 + 링크
        for t in tracks_list:
            sid = t.get("id")
            title = t.get("name")
            track_no = t.get("track_number")
            dur_ms = t.get("duration_ms")
            dur_sec = int(dur_ms / 1000) if isinstance(dur_ms, int) else None

            existing = self.db.execute(
                select(Track).where(Track.spotify_id == sid)
            ).scalars().first()

            if existing:
                existing.title = title or existing.title
                existing.track_no = track_no
                existing.duration_sec = dur_sec
                self.db.add(existing)
                track_ent = existing
            else:
                track_ent = Track(
                    album_id=album_local_id,
                    title=title or "",
                    track_no=track_no,
                    duration_sec=dur_sec,
                    spotify_id=sid,
                    ext_refs={"spotify_url": (t.get("external_urls") or {}).get("spotify")},
                )
                self.db.add(track_ent)
                self.db.flush()  # track_ent.id 확보

            # 아티스트 링크(반드시 DB에 있는 아티스트만)
            arts = t.get("artists") or []
            if not arts and fallback_album_artists:
                arts = list(fallback_album_artists)

            for a in arts:
                spid = a.get("id")
                if not spid:
                    continue
                artist_ent = sp_map[spid]  # require_all 덕분에 KeyError면 로직 버그

                # 중복 링크 방지
                exists_link = self.db.execute(
                    select(TrackArtist).where(
                        and_(
                            TrackArtist.track_id == track_ent.id,
                            TrackArtist.artist_id == artist_ent.id,
                        )
                    )
                ).scalars().first()

                if not exists_link:
                    self.db.add(
                        TrackArtist(
                            track_id=track_ent.id,
                            artist_id=artist_ent.id,
                            role=None,
                        )
                    )

        self.db.flush()