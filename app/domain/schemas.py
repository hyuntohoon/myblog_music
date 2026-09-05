from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Annotated, List, Literal, Optional, Union


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
    # RFC-ui-surface-unification Step 4: primary artist's catalog id so album
    # rows can link straight to the artist hub (spotify_id alone can't build a
    # real href). None for pre-catalog candidate rows (Spotify raw data).
    artist_id: Optional[str] = None
    external_url: Optional[str] = None
    total_tracks: Optional[int] = None
    label: Optional[str] = None
    popularity: Optional[int] = None
    # FEAT-writer-lowfreq-redesign Step 5: editor-set BEST NEW MUSIC flag.
    # Default false so payloads from pre-v0.3.0 DBs (or rows where the column
    # hasn't been written) deserialize cleanly.
    best_new: bool = False


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
    feat_artist_names: List[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    type: str
    items: List[Union[ArtistItem, AlbumItem]] = Field(default_factory=list)


# FEAT-music-search-recall Step 7 (E1): per-row ranking debug entry, surfaced
# only under `?explain=1` for dev triage. `similarity` is the service-layer
# bucket score (0–3) used in ranking, not the raw pg_trgm float.
class ExplainEntry(BaseModel):
    bucket: str                                  # "artist" | "album" | "track"
    id: str
    rank: int                                    # 1-based position within the bucket
    path: str                                    # "decomposed" | "literal" | "expansion"
    matched_field: Optional[str] = None          # "name" | "alias" | "title" | "fuzzy"
    similarity: Optional[float] = None
    popularity: Optional[int] = None


# ✅ 통합 검색 응답 (DB 1번 호출로 3섹션)
class UnifiedSearchResult(BaseModel):
    artists: List[ArtistItem] = Field(default_factory=list)
    albums: List[AlbumItem] = Field(default_factory=list)
    tracks: List[TrackItem] = Field(default_factory=list)
    # Step 7 (E1): populated only when the request passes `?explain=1`. None
    # (omitted intent) in the default response, so existing consumers are
    # unaffected — this is a purely additive contract change.
    debug: Optional[List[ExplainEntry]] = None


# ------- 앨범 상세용 트랙 / 아티스트 / 앨범 -------
class TrackOut(BaseModel):
    id: str
    title: str
    track_no: Optional[int] = None
    duration_sec: Optional[int] = None
    spotify_id: Optional[str] = None
    feat_artist_names: List[str] = Field(default_factory=list)


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
    label: Optional[str] = None
    # FEAT-writer-lowfreq-redesign Step 5: writer reads this when loading the
    # subject card so the BEST NEW pill seeds from the album's current state.
    best_new: bool = False


class AlbumDetail(BaseModel):
    album: AlbumOut
    artists: List[ArtistOut]
    tracks: List[TrackOut]
    meta: dict = Field(default_factory=dict)


# ------- 아티스트 hero (드릴인용) -------
# FEAT-writer-lowfreq-redesign Step 3: writer 의 artist drill-in 패널 hero.
# `status` 는 by-spotify lookup 의 ready/pending 분기를 위한 필드.
# 현 시점에는 absorb-tracking 테이블이 없어 pending 상태를 직접 못 만들지만,
# 스키마에는 유지해서 향후 비동기 흡수 도입 시 응답 모양만 확장하면 되게 둔다.
class CatalogGenreItem(BaseModel):
    """One taxonomy genre chip — label for display, slug for the /genres/?g=
    deep link (RFC-ui-surface-unification Step 5)."""
    label: str
    slug: str


class ArtistHero(BaseModel):
    id: Optional[str] = None
    name: str
    spotify_id: Optional[str] = None
    photo_url: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    # RFC-ui-surface-unification Step 5: the artist's genres in OUR taxonomy,
    # derived from high-confidence album_genres over the artist's catalog
    # (most-tagged first). Replaces the raw Spotify ko-KR `genres` strings as
    # the hub's chip row wherever non-empty — every chip links into the genre
    # map. Empty when no album of the artist has a high-confidence label.
    catalog_genres: List[CatalogGenreItem] = Field(default_factory=list)
    followers: Optional[int] = None
    popularity: Optional[int] = None
    spotify_url: Optional[str] = None
    album_count: int = 0
    track_count: int = 0
    status: str = "ready"  # "ready" | "pending" (현재 prod 에선 "ready" 만 발생)


# ------- 아티스트 id 목록 (정적 빌드 enumeration용) -------
# FEAT-artist-page: 프론트 /artist/[id] 의 getStaticPaths 가 전 카탈로그 아티스트
# (앨범 ≥1)를 빌드타임에 prebuild 하려면 id 목록이 필요한데, 빌드는 DB/런타임 API 를
# 치지 않는다(콘텐츠 컬렉션만 읽음). 이 엔드포인트가 그 목록을 공급한다.
# name 은 prebuild 셸의 <title>/sitemap 용(아일랜드 로드 전 표시).
class ArtistIdItem(BaseModel):
    id: str
    name: str


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


# --- FEAT-youtube-playback-provider Step A2: YouTube candidate search ---

class YouTubeCandidateItem(BaseModel):
    video_id: str
    title: Optional[str] = None
    # Surfaced deliberately: 3 of 20 top results in the Phase 0-A probe were
    # unofficial fan uploads, and the channel is the only field that shows it.
    channel_title: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_sec: Optional[int] = None
    # A HINT FOR A HUMAN, never a validator. Music videos run +5s..+17s long and
    # an edited official video ran -22s on the measured probe.
    duration_delta_sec: Optional[int] = None
    embeddable: bool
    privacy_status: Optional[str] = None
    made_for_kids: Optional[bool] = None
    search_rank: int


class YouTubeCandidateSearchResult(BaseModel):
    track_id: str
    query: str
    track_duration_sec: Optional[int] = None
    candidates: List[YouTubeCandidateItem] = Field(default_factory=list)


SpotifyAlbumId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]
SpotifyMarket = Annotated[str, Field(pattern=r"^[A-Za-z]{2}$")]


class AlbumSyncRequest(BaseModel):
    album_ids: List[SpotifyAlbumId] = Field(min_length=1, max_length=1000)
    market: SpotifyMarket = "KR"


class AlbumSyncAccepted(BaseModel):
    status: Literal["accepted"] = "accepted"
    enqueued_album_ids: List[str] = Field(default_factory=list)
    skipped_existing_album_ids: List[str] = Field(default_factory=list)
    queued_message_count: int = 0


class AlbumSyncFailed(BaseModel):
    status: Literal["failed"] = "failed"
    message: str = "Album sync request could not be accepted"


# ------- "오늘, 이 앨범들" (FEAT-today-buckit Step 1) -------
# 오늘과 같은 월/일에 (과거 연도) 발매된 앨범. release_date IS NOT NULL 인 행만,
# 올해 발매분은 제외(과거 기념일만). 클릭 → 앨범 창(ARCH-entity-interaction-unify)
# 이라 album_id + spotify_album_id + 대표 아티스트 id 를 함께 실어 준다.
class OnThisDayArtist(BaseModel):
    id: str
    name: str
    spotify_id: Optional[str] = None


class OnThisDayItem(BaseModel):
    album_id: str
    spotify_album_id: Optional[str] = None
    title: str
    cover_url: Optional[str] = None
    release_date: str                 # YYYY-MM-DD (repo filters IS NOT NULL)
    years_ago: int                    # today.year - release_date.year
    artists: List[OnThisDayArtist] = Field(default_factory=list)  # primary-first


class OnThisDayResult(BaseModel):
    items: List[OnThisDayItem] = Field(default_factory=list)
    month: int
    day: int
    total: int


# ------- "새 앨범" 피드 (FEAT-release-calendar Track A) -------
# 최근 days일 내 발매된 정규 앨범(album_type='album')만, 대표 아티스트 인기순.
# reviewed_artist: 참여 아티스트가 post_artists 직접 링크 또는
# post_albums→album_artists 경유로 리뷰된 적 있으면 true (홈 카드 마커).
class NewReleaseArtist(BaseModel):
    id: str
    name: str
    popularity: Optional[int] = None


class NewReleaseItem(BaseModel):
    album_id: str
    spotify_album_id: Optional[str] = None
    title: str
    cover_url: Optional[str] = None
    release_date: str                 # YYYY-MM-DD (repo filters IS NOT NULL)
    artists: List[NewReleaseArtist] = Field(default_factory=list)  # primary-first
    artist_popularity: Optional[int] = None  # primary artist popularity (rank key)
    reviewed_artist: bool = False


class NewReleasesResult(BaseModel):
    items: List[NewReleaseItem] = Field(default_factory=list)
    window_days: int
    total: int


# ------- 발매 캘린더 (FEAT-release-calendar Track B Step 6) -------
# artist_release_events는 (source, source_key)당 1행 — 같은 발매를 여러 소스가
# 관측하면 행이 여러 개다. 읽기 경로에서 (artist_id, release_date, 정규화 제목)
# 소프트 그룹핑으로 1개 이벤트로 접어 sources에 관측 소스를 나열한다
# (표시용 그룹핑일 뿐 데이터 병합이 아님 — RFC 설계).
class ReleaseCalendarEvent(BaseModel):
    artist_id: str
    artist_name: str
    artist_popularity: Optional[int] = None
    title: str                              # 우선 소스(spotify>musicbrainz>itunes) 표기 제목
    release_type: Optional[str] = None      # album | ep | single | other
    status: str                             # announced | released (하나라도 released면 released)
    release_date: str                       # YYYY-MM-DD
    sources: List[str] = Field(default_factory=list)  # 정렬된 관측 소스 목록
    spotify_album_id: Optional[str] = None  # 발매일 확정 시에만 세팅됨


class ReleaseCalendarDay(BaseModel):
    date: str                               # YYYY-MM-DD
    events: List[ReleaseCalendarEvent] = Field(default_factory=list)


class ReleaseCalendarResult(BaseModel):
    date_from: str                          # 요청 윈도우 에코백 (YYYY-MM-DD)
    date_to: str
    days: List[ReleaseCalendarDay] = Field(default_factory=list)
    total: int                              # 그룹핑 후 이벤트 수


# ------- 워커/동기화용 입력 -------
class SyncAlbumIn(BaseModel):
    spotify_album_id: str
    market: Optional[str] = "KR"
