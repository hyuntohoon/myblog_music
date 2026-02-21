from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Optional, List, Dict
from app.domain.models import Artist
import time

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
        print(f"[ArtistRepository] search_by_name q={q!r}, limit={limit}, offset={offset}")
        t0 = time.perf_counter()

        stmt = (
            select(Artist)
            .where(Artist.name.ilike(f"%{q}%"))
            .order_by(
                Artist.popularity.desc().nullslast(),
                Artist.followers.desc().nullslast(),
                Artist.views.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        print(f"[ArtistRepository] built stmt: {stmt}")

        try:
            t1 = time.perf_counter()
            result = self.db.execute(stmt)
            t2 = time.perf_counter()
            rows = result.scalars().all()
            t3 = time.perf_counter()

            print(
                "[ArtistRepository] DB exec took "
                f"{t2 - t1:.3f}s, scalars().all() took {t3 - t2:.3f}s, "
                f"total {t3 - t0:.3f}s, rows={len(rows)}"
            )

            return list(rows)

        except Exception as e:
            import traceback
            print("[ArtistRepository] ERROR during search_by_name:", repr(e))
            traceback.print_exc()
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