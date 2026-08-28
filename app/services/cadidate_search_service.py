from typing import Any, Dict, List, Optional, Set
from app.clients.spotify_client import spotify

ALLOWED_TYPES: Set[str] = {"album", "artist", "track"}

class CandidateSearchService:
    """Read-only Spotify candidate search. It never opens DB or SQS clients."""

    # ---------- 외부 API ----------
    def search_candidates(
        self,
        q: str,
        typ: str,
        market: Optional[str],
        limit: int,
        offset: int,
        include_external: Optional[str],
    ) -> Dict[str, Any]:
        wanted = self._normalize_types(typ)                 # ["album","artist","track"]
        type_str = ",".join(wanted)

        data = spotify.search(
            q=q,
            type=type_str,
            market=market,
            limit=limit,
            offset=offset,
            include_external=include_external,
        ) or {}

        # 최소 변경: 분기 제거 + 디스패처
        mappers = {
            "albums": self._map_album_item,
            "artists": self._map_artist_item,
            "tracks": self._map_track_item,
        }

        out: Dict[str, Any] = {}
        for t in wanted:                                     # 요청한 타입만 처리
            key = f"{t}s"
            block = data.get(key) or {}
            if not block:
                continue
            items_raw = block.get("items") or []
            out[key] = [mappers[key](it) for it in items_raw]
            out[f"{key}_pagination"] = self._page_info(block)

        return out

    # ---------- 내부 유틸 ----------
    def _normalize_types(self, typ: str) -> List[str]:
        raw = [t.strip().lower() for t in (typ or "").split(",") if t.strip()]
        # 허용된 것만 남기고, 입력 순서 유지 + 중복 제거
        wanted = [t for t in raw if t in ALLOWED_TYPES]
        wanted = list(dict.fromkeys(wanted))
        if not wanted:
            raise ValueError(f"type must include at least one of {sorted(ALLOWED_TYPES)}")
        return wanted

    @staticmethod
    def _page_info(block: Dict[str, Any]) -> Dict[str, Any]:
        block = block or {}
        return {
            "total": block.get("total"),
            "limit": block.get("limit"),
            "offset": block.get("offset"),
            "next": block.get("next"),
            "previous": block.get("previous"),
            "href": block.get("href"),
        }

    # ---------- 공통 유틸(중복 제거용) ----------
    @staticmethod
    def _first(seq: Optional[List[Any]], default: Any = None) -> Any:
        return (seq or [default])[0]

    @staticmethod
    def _first_image_url(images: Optional[List[Dict[str, Any]]]) -> Optional[str]:
        img = (images or [])
        return img[0].get("url") if img else None

    # ---------- 매핑 ----------
    @staticmethod
    def _map_album_item(a: Dict[str, Any]) -> Dict[str, Any]:
        primary_artist = CandidateSearchService._first(a.get("artists"), {}) or {}
        cover = CandidateSearchService._first_image_url(a.get("images"))
        return {
            "spotify_id": a.get("id"),
            "title": a.get("name"),
            "album_type": a.get("album_type"),
            "release_date": a.get("release_date"),
            "cover_url": cover,
            "artist_name": primary_artist.get("name"),
            "artist_spotify_id": primary_artist.get("id"),
            "external_url": (a.get("external_urls") or {}).get("spotify"),
        }

    @staticmethod
    def _map_artist_item(ar: Dict[str, Any]) -> Dict[str, Any]:
        photo = CandidateSearchService._first_image_url(ar.get("images"))
        return {
            "spotify_id": ar.get("id"),
            "name": ar.get("name"),
            "genres": ar.get("genres") or [],
            "photo_url": photo,
            "external_url": (ar.get("external_urls") or {}).get("spotify"),
        }

    @staticmethod
    def _map_track_item(t: Dict[str, Any]) -> Dict[str, Any]:
        album = t.get("album") or {}
        cover = CandidateSearchService._first_image_url(album.get("images"))
        primary_artist = CandidateSearchService._first(t.get("artists"), {}) or {}
        return {
            "spotify_id": t.get("id"),
            "title": t.get("name"),
            "duration_ms": t.get("duration_ms"),
            "track_number": t.get("track_number"),
            "album": {
                "spotify_id": album.get("id"),
                "title": album.get("name"),
                "release_date": album.get("release_date"),
                "cover_url": cover,
            },
            "artist_name": primary_artist.get("name"),
            "artist_spotify_id": primary_artist.get("id"),
            "external_url": (t.get("external_urls") or {}).get("spotify"),
        }
