from pydantic import BaseModel, Field
from typing import List, Optional

class ArtistItem(BaseModel):
    id: str
    name: str
    spotify_id: Optional[str] = None
    cover_url: Optional[str] = None

class AlbumItem(BaseModel):
    id: str
    title: str
    release_date: Optional[str] = None
    cover_url: Optional[str] = None
    album_type: Optional[str] = None
    spotify_id: Optional[str] = None
    artist_name: Optional[str] = None
    artist_spotify_id: Optional[str] = None

class SearchResult(BaseModel):
    type: str
    items: List[ArtistItem | AlbumItem] = Field(default_factory=list)

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

class AlbumOut(BaseModel):
    id: str
    title: str
    release_date: Optional[str] = None
    cover_url: Optional[str] = None
    album_type: Optional[str] = None
    spotify_id: Optional[str] = None

class AlbumDetail(BaseModel):
    album: AlbumOut
    artists: List[ArtistOut]
    tracks: List[TrackOut]
    meta: dict = {}

class SyncAlbumIn(BaseModel):
    spotify_album_id: str
    market: Optional[str] = "KR"
