# app/domain/models.py
import uuid
from datetime import datetime, date
from typing import Optional, Dict

from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship
from sqlalchemy import Text, Integer, Date, DateTime, ForeignKey, text, Table, Column
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

Base = declarative_base()

# =========================
# Association tables (secondary)
# =========================
album_artists_table = Table(
    "album_artists",
    Base.metadata,
    Column(
        "album_id",
        PGUUID(as_uuid=True),
        ForeignKey("albums.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "artist_id",
        PGUUID(as_uuid=True),
        ForeignKey("artists.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("role", Text),
)

track_artists_table = Table(
    "track_artists",
    Base.metadata,
    Column(
        "track_id",
        PGUUID(as_uuid=True),
        ForeignKey("tracks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "artist_id",
        PGUUID(as_uuid=True),
        ForeignKey("artists.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("role", Text),
)

# =========================
# Models
# =========================
class Artist(Base):
    __tablename__ = "artists"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    spotify_id: Mapped[Optional[str]] = mapped_column(Text)
    photo_url: Mapped[Optional[str]] = mapped_column(Text)  # 스키마에 존재하므로 매핑
    ext_refs: Mapped[Dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"))

    # relationships
    albums: Mapped[list["Album"]] = relationship(
        "Album",
        secondary=album_artists_table,
        back_populates="artists",
        lazy="select",
    )
    tracks: Mapped[list["Track"]] = relationship(
        "Track",
        secondary=track_artists_table,
        back_populates="artists",
        lazy="select",
    )


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

    # relationships
    artists: Mapped[list[Artist]] = relationship(
        "Artist",
        secondary=album_artists_table,
        back_populates="albums",
        lazy="select",
    )
    tracks: Mapped[list["Track"]] = relationship(
        "Track",
        back_populates="album",
        cascade="all, delete-orphan",
        lazy="select",
    )


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

    # relationships
    album: Mapped[Album] = relationship(
        "Album",
        back_populates="tracks",
        lazy="select",
    )
    artists: Mapped[list[Artist]] = relationship(
        "Artist",
        secondary=track_artists_table,
        back_populates="tracks",
        lazy="select",
    )