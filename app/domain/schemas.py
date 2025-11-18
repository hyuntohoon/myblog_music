from pydantic import BaseModel, Field
from typing import List, Optional


# ------- 리스트용 아티스트 (검색 결과 등) -------
class ArtistItem(BaseModel):
    id: str
    name: str
    spotify_id: Optional[str] = None
    cover_url: Optional[str] = None          # artists.photo_url 매핑
    genres: List[str] = Field(default_factory=list)
    followers_count: Optional[int] = None
    popularity: Optional[int] = None
    spotify_url: Optional[str] = None        # artists.spotify_url or ext_refs["spotify_url"]


# ------- 리스트용 앨범 (검색 결과 등) -------
class AlbumItem(BaseModel):
    id: str
    title: str
    release_date: Optional[str] = None
    cover_url: Optional[str] = None
    album_type: Optional[str] = None
    spotify_id: Optional[str] = None
    artist_name: Optional[str] = None
    artist_spotify_id: Optional[str] = None
    external_url: Optional[str] = None     
    total_tracks: Optional[int] = None
    label: Optional[str] = None
    popularity: Optional[int] = None  # ext_refs / external_urls.spotify


class SearchResult(BaseModel):
    type: str
    items: List[ArtistItem | AlbumItem] = Field(default_factory=list)


# ------- 앨범 상세용 트랙 / 아티스트 / 앨범 -------
class TrackOut(BaseModel):
    id: str
    title: str
    track_no: Optional[int] = None
    duration_sec: Optional[int] = None
    spotify_id: Optional[str] = None


class ArtistOut(BaseModel):
    id: str
    name: str
    spotify_id: Optional[str] = None
    photo_url: Optional[str] = None          # artists.photo_url
    genres: List[str] = Field(default_factory=list)
    followers_count: Optional[int] = None
    popularity: Optional[int] = None
    spotify_url: Optional[str] = None        # artists.spotify_url


class AlbumOut(BaseModel):
    id: str
    title: str
    release_date: Optional[str] = None
    cover_url: Optional[str] = None
    album_type: Optional[str] = None
    spotify_id: Optional[str] = None
    external_url: Optional[str] = None       # ext_refs / external_urls.spotify


class AlbumDetail(BaseModel):
    album: AlbumOut
    artists: List[ArtistOut]
    tracks: List[TrackOut]
    meta: dict = Field(default_factory=dict)


# ------- 워커/동기화용 입력 -------
class SyncAlbumIn(BaseModel):
    spotify_album_id: str
    market: Optional[str] = "KR"