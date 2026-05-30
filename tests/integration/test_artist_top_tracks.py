"""FEAT-writer-lowfreq-redesign Step 3 — top-tracks ordering integration.

Mock-based unit tests in tests/test_artists_api.py prove routing and the
hero shape, but they cannot verify the four-column ORDER BY in
`TrackRepository.list_top_tracks_by_artist`. Per
[[feedback-sa-session-lifecycle-mock-blind]] new SELECT logic over a JOIN
needs at least one real-engine test.

Seed pattern: one artist with 4 tracks across 2 albums, crafted so each
ordering tie-breaker has to fire to produce the expected sequence.

Skipped when TEST_DB_URL is unset (local matrix without Neon test branch).
"""
from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")

from myblog_shared_db.models import (  # noqa: E402
    Album,
    Artist,
    Track,
    album_artists_table,
    track_artists_table,
)

from app.repositories.artist_repo import ArtistRepository  # noqa: E402
from app.repositories.track_repo import TrackRepository  # noqa: E402

_TEST_DB_URL = os.environ.get("TEST_DB_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _TEST_DB_URL,
        reason="integration test requires TEST_DB_URL env var (Neon test branch)",
    ),
]


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(_TEST_DB_URL, pool_pre_ping=True, future=True)
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_name FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'tracks'
                   AND column_name IN ('views', 'track_no')
                """
            )
        ).fetchall()
    if len({r[0] for r in rows}) < 2:
        eng.dispose()
        pytest.skip("Neon test branch missing tracks.views / track_no")
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    conn = engine.connect()
    txn = conn.begin()
    Session = sessionmaker(bind=conn, autoflush=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        txn.rollback()
        conn.close()


def test_top_tracks_ordering_exercises_all_tiebreakers(session):
    """Build a corpus where the expected ordering is t_views_high, t_pop_high,
    t_recent, t_lowtrackno — each row wins by a different tie-breaker.
    """
    artist = Artist(
        id=uuid.uuid4(),
        name=f"TopTrackArtist-{uuid.uuid4().hex[:8]}",
        spotify_id=f"sp_tta_{uuid.uuid4().hex[:10]}",
        popularity=70,
        followers=1000,
    )
    album_high_pop = Album(
        id=uuid.uuid4(),
        title="HighPop",
        spotify_id=f"sp_alb_hp_{uuid.uuid4().hex[:10]}",
        release_date=date(2020, 1, 1),
        popularity=80,
    )
    album_recent_lowpop = Album(
        id=uuid.uuid4(),
        title="RecentLowPop",
        spotify_id=f"sp_alb_rlp_{uuid.uuid4().hex[:10]}",
        release_date=date(2025, 6, 1),
        popularity=10,
    )

    # 4 tracks, all on artist; views: 100, 0, 0, 0 (so 1st by views, then
    # 3 ties decided by album.popularity / release_date / track_no).
    t_views_high = Track(
        id=uuid.uuid4(),
        album_id=album_recent_lowpop.id,
        title="HighViews",
        spotify_id=f"sp_trk_v_{uuid.uuid4().hex[:10]}",
        views=100,
        track_no=10,
    )
    t_pop_high = Track(
        id=uuid.uuid4(),
        album_id=album_high_pop.id,  # wins the views=0 tie by album popularity
        title="OnHighPopAlbum",
        spotify_id=f"sp_trk_p_{uuid.uuid4().hex[:10]}",
        views=0,
        track_no=5,
    )
    t_recent = Track(
        id=uuid.uuid4(),
        album_id=album_recent_lowpop.id,  # next: same low pop but newer release
        title="RecentLowPopTrack3",
        spotify_id=f"sp_trk_r_{uuid.uuid4().hex[:10]}",
        views=0,
        track_no=3,
    )
    t_lowtrackno = Track(
        id=uuid.uuid4(),
        album_id=album_recent_lowpop.id,  # same album → tie → lower track_no wins
        title="RecentLowPopTrack1",
        spotify_id=f"sp_trk_l_{uuid.uuid4().hex[:10]}",
        views=0,
        track_no=1,
    )

    session.add_all([
        artist,
        album_high_pop,
        album_recent_lowpop,
        t_views_high,
        t_pop_high,
        t_recent,
        t_lowtrackno,
    ])
    session.flush()

    session.execute(album_artists_table.insert().values([
        {"album_id": album_high_pop.id, "artist_id": artist.id, "role": None},
        {"album_id": album_recent_lowpop.id, "artist_id": artist.id, "role": None},
    ]))
    session.execute(track_artists_table.insert().values([
        {"track_id": t_views_high.id, "artist_id": artist.id, "role": None},
        {"track_id": t_pop_high.id, "artist_id": artist.id, "role": None},
        {"track_id": t_recent.id, "artist_id": artist.id, "role": None},
        {"track_id": t_lowtrackno.id, "artist_id": artist.id, "role": None},
    ]))
    session.flush()

    repo = TrackRepository(session, ArtistRepository(session))
    got = repo.list_top_tracks_by_artist(artist.id, limit=10)
    ids = [str(t.id) for t in got]

    # Order must be: views=100 wins → high-pop album wins the views=0 tie →
    # between the two remaining (same album, so same popularity AND
    # release_date) the lower track_no wins (track_no=1 before track_no=3).
    # All four ORDER BY columns are exercised in series.
    assert ids == [
        str(t_views_high.id),
        str(t_pop_high.id),
        str(t_lowtrackno.id),
        str(t_recent.id),
    ], (
        f"ordering wrong:\ngot={ids}\nexpected views_high → pop_high → "
        f"lowtrackno → recent (all 4 tie-breakers exercised)"
    )
