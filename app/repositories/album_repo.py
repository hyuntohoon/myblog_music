# app/repositories/album_repo.py
from typing import Optional, List, Iterable, Tuple, Dict, Iterable, Set
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from typing import Callable, Tuple, List, Dict
from sqlalchemy.sql.elements import BinaryExpression
from app.domain.models import Album, Artist, album_artists_table
from sqlalchemy.dialects.postgresql import insert as pg_insert

class AlbumRepository:
    def __init__(self, db: Session):
        self.db = db

    # 단건 + artists/tracks까지 한 번에 로딩
    def get_by_id(self, album_id: str) -> Optional[Album]:
        stmt = (
            select(Album)
            .options(
                selectinload(Album.artists),
                selectinload(Album.tracks),
            )
            .where(Album.id == album_id)
        )
        return self.db.execute(stmt).scalars().first()

    def get_by_spotify_id(self, spotify_id: str) -> Optional[Album]:
        stmt = (
            select(Album)
            .options(
                selectinload(Album.artists),
                selectinload(Album.tracks),
            )
            .where(Album.spotify_id == spotify_id)
        )
        return self.db.execute(stmt).scalars().first()

    def search_by_title(self, q: str, limit: int, offset: int) -> List[Album]:
        # 필요 시 artists 미리 로딩해서 N+1 방지
        stmt = (
            select(Album)
            .options(selectinload(Album.artists))
            .where(Album.title.ilike(f"%{q}%"))
            .limit(limit)
            .offset(offset)
            .order_by(Album.popularity.desc().nullslast())
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
        # artists/tracks는 이후 링크 함수에서 연결
        return ent

    # ✅ secondary 테이블에 직접 insert
    def link_album_artists(self, album_id: str, artist_ids: Iterable[str]):
        rows = [{"album_id": album_id, "artist_id": aid, "role": None} for aid in artist_ids]
        if not rows:
            return
        stmt = pg_insert(album_artists_table).values(rows)
        # (album_id, artist_id) 복합 PK 기준으로 중복 무시
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["album_id", "artist_id"]
        )
        self.db.execute(stmt)
        self.db.flush()

    # ✅ secondary 테이블로 조인해서 앨범 + 아티스트 반환
    def get_with_artists(self, album_id: str) -> Tuple[Optional[Album], List[Artist]]:
        album = self.get_by_id(album_id)
        if not album:
            return None, []
        artists_stmt = (
            select(Artist)
            .join(album_artists_table, album_artists_table.c.artist_id == Artist.id)
            .where(album_artists_table.c.album_id == album_id)
        )
        artists = list(self.db.execute(artists_stmt).scalars().all())
        return album, artists

    # ✅ 앨범들에 대한 '대표 아티스트'(첫 번째 아티스트) 맵 생성
    # 반환: { album_id(str): (artist_name or None, artist_spotify_id or None) }
    def get_primary_artist_map(self, album_ids: List[str]) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
        if not album_ids:
            return {}

        rows = self.db.execute(
            select(Album.id, Artist.name, Artist.spotify_id)
            .join(album_artists_table, album_artists_table.c.album_id == Album.id)
            .join(Artist, album_artists_table.c.artist_id == Artist.id)
            .where(Album.id.in_(album_ids))
        ).all()

        result: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        for al_id, ar_name, ar_spid in rows:
            # 첫 번째로 본 아티스트를 대표로 고정
            if str(al_id) not in result:
                result[str(al_id)] = (ar_name, ar_spid)
        return result
    
    import time
    from typing import Iterable, List, Set

    def get_existing_spotify_ids(self, ids: Iterable[str]) -> Set[str]:
        ids_list: List[str] = [i for i in ids if i]
        print(f"[AlbumRepository] get_existing_spotify_ids called with {len(ids_list)} ids")
        if not ids_list:
            print("[AlbumRepository] empty id list, returning empty set")
            return set()

        try:
            print(f"[AlbumRepository] executing select for first 5 ids: {ids_list[:5]}")

            t0 = time.perf_counter()
            # 세션에서 커넥션 뽑는 타이밍도 보고 싶으면 이렇게 한 번 강제로 연결
            print("[AlbumRepository] acquiring DB connection...")
            conn = self.db.connection()
            print("[AlbumRepository] DB connection acquired in "
                f"{time.perf_counter() - t0:.3f}s")

            t1 = time.perf_counter()
            stmt = select(Album.spotify_id).where(Album.spotify_id.in_(ids_list))
            print("[AlbumRepository] statement built")

            print("[AlbumRepository] executing scalars()...")
            scalars = self.db.scalars(stmt)
            t2 = time.perf_counter()
            print("[AlbumRepository] scalars() returned in "
                f"{t2 - t1:.3f}s, now calling .all()")

            rows = scalars.all()
            t3 = time.perf_counter()
            print("[AlbumRepository] .all() returned in "
                f"{t3 - t2:.3f}s, total DB time {t3 - t0:.3f}s")

            print(f"[AlbumRepository] query done, fetched {len(rows)} rows")
            return set(rows)

        except Exception as e:
            import traceback
            print("[AlbumRepository] ERROR during DB query:", repr(e))
            traceback.print_exc()
            return set()


    # ---- 공통 내부 함수 ----
    def _list_by_artistId_artist_filter(
        self,
        *,
        filter_expr: BinaryExpression,
        limit: int,
        offset: int,
    ) -> Tuple[List[Album], Dict[str, tuple[str | None, str | None]]]:

        stmt = (
            select(Album, Artist)
            .join(album_artists_table, album_artists_table.c.album_id == Album.id)
            .join(Artist, album_artists_table.c.artist_id == Artist.id)
            .where(filter_expr)   # ← 조건만 다름
            .order_by(
                Album.popularity.desc().nullslast(),
                Album.release_date.desc().nullslast(),
            )
            .limit(limit)
            .offset(offset)
        )

        rows = self.db.execute(stmt).all()

        albums: List[Album] = []
        primary_map: Dict[str, tuple[str | None, str | None]] = {}

        for al, ar in rows:
            albums.append(al)
            if al.id not in primary_map:
                primary_map[al.id] = (ar.name, ar.spotify_id)

        return albums, primary_map

    # ---- 기존 wrapper: artist_id 기반 ----
    def list_by_artistId_artist(
        self,
        *,
        artist_id: str,
        limit: int,
        offset: int,
    ):
        return self._list_by_artistId_artist_filter(
            filter_expr=(Artist.id == artist_id),
            limit=limit,
            offset=offset,
        )

    # ---- 새 wrapper: spotify_id 기반 ----
    def list_by_spotify_artist(
        self,
        *,
        spotify_id: str,
        limit: int,
        offset: int,
    ):
        return self._list_by_artistId_artist_filter(
            filter_expr=(Artist.spotify_id == spotify_id),
            limit=limit,
            offset=offset,
        )
    
    def get_by_spotify_id(self, spotify_id: str) -> Album | None:
        stmt = select(Album).where(Album.spotify_id == spotify_id)
        return self.db.execute(stmt).scalar_one_or_none()