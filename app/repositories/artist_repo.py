import logging
from sqlalchemy.orm import Session
from sqlalchemy import or_, select, text
from typing import Optional, List, Dict
from myblog_shared_db.models import Artist

logger = logging.getLogger(__name__)


class ArtistRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_spotify_id(self, spotify_id: str) -> Optional[Artist]:
        return self.db.execute(
            select(Artist).where(Artist.spotify_id == spotify_id)
        ).scalars().first()

    def get_by_id(self, artist_id: str) -> Optional[Artist]:
        return self.db.execute(
            select(Artist).where(Artist.id == artist_id)
        ).scalars().first()

    def search_by_name(self, q: str, limit: int, offset: int) -> List[Artist]:
        # Match on Artist.name (substring, case-insensitive) OR any element of the
        # MusicBrainz-populated `aliases` JSONB array. Aliases let users find
        # Korean transliterations and alternate spellings.
        pat = f"%{q}%"
        alias_match = text(
            "EXISTS (SELECT 1 FROM jsonb_array_elements_text(artists.aliases) AS e WHERE e ILIKE :alias_pat)"
        ).bindparams(alias_pat=pat)
        stmt = (
            select(Artist)
            .where(or_(Artist.name.ilike(pat), alias_match))
            .order_by(
                Artist.popularity.desc().nullslast(),
                Artist.followers.desc().nullslast(),
                Artist.views.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        try:
            return list(self.db.execute(stmt).scalars().all())
        except Exception as e:
            logger.error("search_by_name failed for q=%r: %s", q, e, exc_info=True)
            return []


    # 여러 spotify_id를 한 번에 조회
    def get_map_by_spotify_ids(self, spotify_ids: List[str]) -> Dict[str, Artist]:
        if not spotify_ids:
            return {}
        rows = self.db.execute(
            select(Artist).where(Artist.spotify_id.in_(spotify_ids))
        ).scalars().all()
        return {a.spotify_id: a for a in rows if a.spotify_id}

    # 반드시 모두 DB에 있어야 함(없으면 예외)
    def require_all_by_spotify_ids(self, spotify_ids: List[str]) -> Dict[str, Artist]:
        m = self.get_map_by_spotify_ids(spotify_ids)
        missing = sorted(set(spotify_ids) - set(m.keys()))
        if missing:
            # 서비스/라우터에서 422/409로 매핑
            raise LookupError(f"Missing artists in DB (spotify_id): {missing}")
        return m

    # 필요 시 사용할 수 있는 최소 업서트(사용 안 하면 지워도 됨)
    def upsert_min(
        self,
        *,
        spotify_id: str,
        name: str,
        photo_url: str | None = None,
        ext_refs: dict | None = None,
    ) -> Artist:
        ent = self.get_by_spotify_id(spotify_id)
        if ent:
            if name:
                ent.name = name
            if photo_url is not None:
                ent.photo_url = photo_url
            if ext_refs:
                ent.ext_refs = {**(ent.ext_refs or {}), **ext_refs}
            self.db.add(ent)
            return ent

        ent = Artist(
            spotify_id=spotify_id,
            name=name,
            photo_url=photo_url,
            ext_refs=ext_refs or {},
        )
        self.db.add(ent)
        return ent