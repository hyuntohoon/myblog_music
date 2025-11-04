from sqlalchemy.orm import Session
from sqlalchemy import select, and_
from typing import Optional, List, Iterable
from app.domain.models import Album, AlbumArtist

class AlbumRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, album_id: str) -> Optional[Album]:
        return self.db.execute(
            select(Album).where(Album.id == album_id)
        ).scalars().first()

    def get_by_spotify_id(self, spotify_id: str) -> Optional[Album]:
        return self.db.execute(
            select(Album).where(Album.spotify_id == spotify_id)
        ).scalars().first()

    def search_by_title(self, q: str, limit: int, offset: int) -> List[Album]:
        stmt = (
            select(Album)
            .where(Album.title.ilike(f"%{q}%"))
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars().all())

    def upsert_album_min(
        self,
        *,
        spotify_id: str,
        title: str,
        release_date,
        cover_url: str | None,
        album_type: str | None,
        ext_refs: dict | None,
    ) -> Album:
        ent = self.get_by_spotify_id(spotify_id)
        if ent:
            ent.title = title or ent.title
            ent.release_date = release_date
            ent.cover_url = cover_url
            ent.album_type = album_type
            if ext_refs:
                ent.ext_refs = {**(ent.ext_refs or {}), **ext_refs}
            self.db.add(ent)
            return ent
        ent = Album(
            spotify_id=spotify_id,
            title=title or "",
            release_date=release_date,
            cover_url=cover_url,
            album_type=album_type,
            ext_refs=ext_refs or {},
        )
        self.db.add(ent)
        return ent

    def link_album_artists(self, album_id: str, artist_ids: Iterable[str]):
        for aid in artist_ids:
            # 중복 링크 방지
            exists_link = self.db.execute(
                select(AlbumArtist).where(
                    and_(
                        AlbumArtist.album_id == album_id,
                        AlbumArtist.artist_id == aid,
                    )
                )
            ).scalars().first()
            if not exists_link:
                self.db.add(AlbumArtist(album_id=album_id, artist_id=aid))
        self.db.flush()