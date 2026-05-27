from __future__ import annotations

from pydantic import BaseModel, Field
from typing import List, Optional, Union


# ------- 리스트용 아티스트 (검색 결과 등) -------
class ArtistItem(BaseModel):
    id: str
    name: str
    spotify_id: Optional[str] = None
    cover_url: Optional[str] = None          # artists.photo_url 매핑
    genres: List[str] = Field(default_factory=list)
    followers_count: Optional[int] = None
    popularity: Optional[int] = None
    spotify_url: Optional[str] = None


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
    popularity: Optional[int] = None


# ✅ 리스트용 트랙 (통합 검색에서 사용)
class TrackItem(BaseModel):
    id: str
    title: str
    track_no: Optional[int] = None
    duration_sec: Optional[int] = None
    spotify_id: Optional[str] = None

    # ✅ 트랙 클릭 → 앨범 상세로 이동하기 위한 핵심
    album_id: str
    album_title: Optional[str] = None
    cover_url: Optional[str] = None
    release_date: Optional[str] = None
    album_spotify_id: Optional[str] = None

    # UI 서브텍스트용
    artist_name: Optional[str] = None


class SearchResult(BaseModel):
    type: str
    items: List[Union[ArtistItem, AlbumItem]] = Field(default_factory=list)


# ✅ 통합 검색 응답 (DB 1번 호출로 3섹션)
class UnifiedSearchResult(BaseModel):
    artists: List[ArtistItem] = Field(default_factory=list)
    albums: List[AlbumItem] = Field(default_factory=list)
    tracks: List[TrackItem] = Field(default_factory=list)


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
    photo_url: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    followers_count: Optional[int] = None
    popularity: Optional[int] = None
    spotify_url: Optional[str] = None


class AlbumOut(BaseModel):
    id: str
    title: str
    release_date: Optional[str] = None
    cover_url: Optional[str] = None
    album_type: Optional[str] = None
    spotify_id: Optional[str] = None
    external_url: Optional[str] = None


class AlbumDetail(BaseModel):
    album: AlbumOut
    artists: List[ArtistOut]
    tracks: List[TrackOut]
    meta: dict = Field(default_factory=dict)


# ------- 후보 검색(Spotify-passthrough) 응답 -------
# 주의: 후보 응답 아이템은 DB 검색용 AlbumItem/ArtistItem과 별개다.
# DB가 아직 모르는 항목이라 `id` (DB UUID)가 없고 `spotify_id`만 있다.
class CandidatePagination(BaseModel):
    total: Optional[int] = None
    limit: Optional[int] = None
    offset: Optional[int] = None
    next: Optional[str] = None
    previous: Optional[str] = None
    href: Optional[str] = None


class CandidateAlbumItem(BaseModel):
    spotify_id: Optional[str] = None
    title: Optional[str] = None
    album_type: Optional[str] = None
    release_date: Optional[str] = None
    cover_url: Optional[str] = None
    artist_name: Optional[str] = None
    artist_spotify_id: Optional[str] = None
    external_url: Optional[str] = None


class CandidateArtistItem(BaseModel):
    spotify_id: Optional[str] = None
    name: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    photo_url: Optional[str] = None
    external_url: Optional[str] = None


class CandidateTrackAlbumRef(BaseModel):
    spotify_id: Optional[str] = None
    title: Optional[str] = None
    release_date: Optional[str] = None
    cover_url: Optional[str] = None


class CandidateTrackItem(BaseModel):
    spotify_id: Optional[str] = None
    title: Optional[str] = None
    duration_ms: Optional[int] = None
    track_number: Optional[int] = None
    album: Optional[CandidateTrackAlbumRef] = None
    artist_name: Optional[str] = None
    artist_spotify_id: Optional[str] = None
    external_url: Optional[str] = None


# 요청한 type에 포함되지 않은 섹션은 응답에서 생략됨 (라우터가 response_model_exclude_none=True 적용).
class CandidateSearchResult(BaseModel):
    albums: Optional[List[CandidateAlbumItem]] = None
    artists: Optional[List[CandidateArtistItem]] = None
    tracks: Optional[List[CandidateTrackItem]] = None
    albums_pagination: Optional[CandidatePagination] = None
    artists_pagination: Optional[CandidatePagination] = None
    tracks_pagination: Optional[CandidatePagination] = None


# ------- 워커/동기화용 입력 -------
class SyncAlbumIn(BaseModel):
    spotify_album_id: str
    market: Optional[str] = "KR"