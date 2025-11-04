# app/domain/models.py (상단)
import uuid  # ✅ 추가
from datetime import datetime, date
from typing import Optional, Dict

from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import Text, Integer, Date, DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

Base = declarative_base()

# ✅ PK는 DB에서 생성 (gen_random_uuid), 파이썬에서 만들지 않음
class Artist(Base):
    __tablename__ = "artists"
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    spotify_id: Mapped[Optional[str]] = mapped_column(Text)
    ext_refs: Mapped[Dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"))

class Album(Base):
    __tablename__ = "albums"
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    release_date: Mapped[Optional[date]] = mapped_column(Date)
    cover_url: Mapped[Optional[str]] = mapped_column(Text)
    album_type: Mapped[Optional[str]] = mapped_column(Text)
    spotify_id: Mapped[Optional[str]] = mapped_column(Text)
    ext_refs: Mapped[Dict] = mapped_column(JSONB, default=dict)
    views: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"))

class AlbumArtist(Base):
    __tablename__ = "album_artists"
    album_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("albums.id", ondelete="CASCADE"),
        primary_key=True,
    )
    artist_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("artists.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[Optional[str]] = mapped_column(Text)

class Track(Base):
    __tablename__ = "tracks"
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    album_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("albums.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    track_no: Mapped[Optional[int]] = mapped_column(Integer)
    duration_sec: Mapped[Optional[int]] = mapped_column(Integer)
    spotify_id: Mapped[Optional[str]] = mapped_column(Text)
    ext_refs: Mapped[Dict] = mapped_column(JSONB, default=dict)
    views: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"))

class TrackArtist(Base):
    __tablename__ = "track_artists"
    track_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tracks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    artist_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("artists.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[Optional[str]] = mapped_column(Text)