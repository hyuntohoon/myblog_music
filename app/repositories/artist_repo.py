from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Optional, List, Dict
from app.domain.models import Artist

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
        stmt = (
            select(Artist)
            .where(Artist.name.ilike(f"%{q}%"))
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars().all())

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
    def upsert_min(self, *, spotify_id: str, name: str, ext_refs: dict | None) -> Artist:
        ent = self.get_by_spotify_id(spotify_id)
        if ent:
            if name:
                ent.name = name
            if ext_refs:
                ent.ext_refs = {**(ent.ext_refs or {}), **ext_refs}
            self.db.add(ent)
            return ent
        ent = Artist(spotify_id=spotify_id, name=name, ext_refs=ext_refs or {})
        self.db.add(ent)
        # Text PK + default=gen_uuid 이면 객체 생성 시점에 id가 들어가므로 flush 불필요
        return ent