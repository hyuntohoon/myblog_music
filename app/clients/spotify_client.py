import base64, time, httpx
from typing import Optional, Dict, Any, List
from app.core.config import settings

class SpotifyClient:
    def __init__(self):
        self._token: Optional[str] = None
        self._exp: float = 0.0

    def _get_token(self) -> str:
        now = time.time()
        if self._token and now < self._exp:
            return self._token

        auth = f"{settings.SPOTIFY_CLIENT_ID}:{settings.SPOTIFY_CLIENT_SECRET}".encode()
        headers = {
            "Authorization": "Basic " + base64.b64encode(auth).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}
        r = httpx.post(settings.SPOTIFY_TOKEN_URL, headers=headers, data=data, timeout=20)
        r.raise_for_status()
        payload = r.json()
        self._token = payload["access_token"]
        # 만료 90% 지점으로 앞당겨 재발급
        self._exp = now + float(payload.get("expires_in", 3600)) * 0.9
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # ✅ 범용 검색 래퍼: /v1/search
    def search(
        self,
        *,
        q: str,
        type: str,                      # "album,artist,track" 등 콤마 구분 문자열
        market: Optional[str] = None,   # 예: "KR"
        limit: int = 20,
        offset: int = 0,
        include_external: Optional[str] = None,  # "audio" 만 유효
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "q": q,
            "type": type,
            "limit": limit,
            "offset": offset,
        }
        # market 우선순위: 인자 > 설정값
        mkt = market or getattr(settings, "SPOTIFY_DEFAULT_MARKET", None)
        if mkt:
            params["market"] = mkt
        if include_external:
            params["include_external"] = include_external

        r = httpx.get(f"{settings.SPOTIFY_API_BASE}/search", headers=self._headers(), params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    # 유지: 앨범 전용 검색 (필드 필터 조합)
    def search_albums(self, *, album: str, artist: str | None, limit: int = 5, market: str | None = None) -> Dict[str, Any]:
        q = f'album:"{album}"'
        if artist:
            q += f' artist:"{artist}"'
        params = {"q": q, "type": "album", "limit": limit}
        if market or settings.SPOTIFY_DEFAULT_MARKET:
            params["market"] = market or settings.SPOTIFY_DEFAULT_MARKET
        r = httpx.get(f"{settings.SPOTIFY_API_BASE}/search", headers=self._headers(), params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def get_album(self, album_id: str, market: str | None = None) -> Dict[str, Any]:
        params = {}
        mkt = market or getattr(settings, "SPOTIFY_DEFAULT_MARKET", None)
        if mkt:
            params["market"] = mkt
        r = httpx.get(f"{settings.SPOTIFY_API_BASE}/albums/{album_id}", headers=self._headers(), params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    # 페이지네이션 안정화: next URL 따라가기
    def get_album_tracks_all(self, album_id: str, market: str | None = None, page_size: int = 50):
        params = {"limit": page_size, "offset": 0}
        mkt = market or getattr(settings, "SPOTIFY_DEFAULT_MARKET", None)
        if mkt:
            params["market"] = mkt

        url = f"{settings.SPOTIFY_API_BASE}/albums/{album_id}/tracks"
        items = []
        while True:
            r = httpx.get(url, headers=self._headers(), params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            items.extend(data.get("items", []))
            next_url = data.get("next")
            if not next_url:
                break
            # next가 절대경로이므로, 다음 요청은 url만 교체하고 params는 초기화
            url = next_url
            params = {}
        return items
    
    def get_artists(self, ids: List[str]) -> List[Dict[str, Any]]:
        if not ids:
            return []
        params = {"ids": ",".join(ids)}
        r = httpx.get(
            f"{settings.SPOTIFY_API_BASE}/artists",
            headers=self._headers(),
            params=params,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("artists", [])

spotify = SpotifyClient()