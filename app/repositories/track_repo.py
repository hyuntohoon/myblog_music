from __future__ import annotations

from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select
from typing import Iterable, List

from myblog_shared_db.models import Track, Album, track_artists_table
from app.repositories.artist_repo import ArtistRepository


class TrackRepository:
    def __init__(self, db: Session, artist_repo: ArtistRepository):
        self.db = db
        self.artist_repo = artist_repo

    def get_by_album(self, album_id: str) -> List[Track]:
        return list(
            self.db.execute(
                select(Track)
                .options(selectinload(Track.artists))
                .where(Track.album_id == album_id)
                .order_by(Track.track_no.asc().nullslast())
            )
            .scalars()
            .all()
        )

    # ✅ 추가: title 기반 트랙 검색(DB)
    def search_by_title(self, q: str, limit: int, offset: int) -> List[Track]:
        stmt = (
            select(Track)
            .options(
                selectinload(Track.album).selectinload(Album.artists),  # album_title/cover + 대표 artist용
                selectinload(Track.artists),                            # track artist 우선
            )
            .where(Track.title.ilike(f"%{q}%"))
            .order_by(Track.views.desc(), Track.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars().all())

    # BUG-19 expansion: tracks for a single matched artist, capped at LIMIT
    # at the SQL layer per Q2 (default 50, "not post-fetch"), ordered by
    # Album.release_date DESC NULLS LAST (no Track.popularity column today).
    def list_by_artist_id(self, artist_id, limit: int = 50) -> List[Track]:
        stmt = (
            select(Track)
            .options(
                selectinload(Track.album).selectinload(Album.artists),
                selectinload(Track.artists),
            )
            .join(track_artists_table, track_artists_table.c.track_id == Track.id)
            .join(Album, Track.album_id == Album.id)
            .where(track_artists_table.c.artist_id == artist_id)
            .order_by(Album.release_date.desc().nullslast())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    # BUG-19 expansion: tracks for matched album ids, bulk-loaded.
    # Used when an album literal-matched and we need its tracks for the track bucket.
    def list_by_album_ids(self, album_ids: List) -> List[Track]:
        if not album_ids:
            return []
        stmt = (
            select(Track)
            .options(
                selectinload(Track.album).selectinload(Album.artists),
                selectinload(Track.artists),
            )
            .where(Track.album_id.in_(album_ids))
            .order_by(Track.track_no.asc().nullslast())
        )
        return list(self.db.execute(stmt).scalars().all())

    def upsert_tracks_with_artists_db_only(
        self,
        *,
        album_local_id: str,
        tracks_json: Iterable[dict],
        fallback_album_artists: Iterable[dict] | None = None,
    ):
        # 필요한 모든 spotify_artist_id 수집
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

        # DB에서만 로드 (없으면 예외)
        sp_map = self.artist_repo.require_all_by_spotify_ids(sorted(need_sp_ids))

        # 트랙 업서트 + 아티스트 링크
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
                    ext_refs={
                        "spotify_url": (t.get("external_urls") or {}).get("spotify")
                    },
                )
                self.db.add(track_ent)
                self.db.flush()  # track_ent.id 확보

            # 아티스트 연결
            arts = t.get("artists") or []
            if not arts and fallback_album_artists:
                arts = list(fallback_album_artists)

            existing_ids = {str(a.id) for a in track_ent.artists or []}

            for a in arts:
                spid = a.get("id")
                if not spid or spid not in sp_map:
                    continue
                artist_ent = sp_map[spid]
                if str(artist_ent.id) not in existing_ids:
                    track_ent.artists.append(artist_ent)

        self.db.flush()