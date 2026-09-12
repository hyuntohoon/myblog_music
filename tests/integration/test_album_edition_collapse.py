"""DATA-release-noise (c) integration — duplicate editions collapse in real SQL.

The unit tests in `tests/test_album_editions.py` prove the key and the survivor
rule over plain objects. They cannot prove the two things that actually broke in
production ([[feedback-sa-session-lifecycle-mock-blind]]):

1. that the collapse sits on the read paths that were serving duplicates —
   `/api/music/search/unified` returned ICEMAN twice and Drake's discography
   listed it twice (2026-07-26 audit), both verified live on 2026-09-03;
2. that reading `Album.artists` for the group key does not add a query per
   album — the discography query never touched that relationship before, so the
   eager load in `album_repo` is load-bearing, not decoration.

Both assertions need real SQL, so they live here. Guarded by `TEST_DB_URL` like
the other integration suites.
"""
from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")

from myblog_shared_db.models import (  # noqa: E402
    Album,
    Artist,
    Base,
    album_artists_table,
)

from app.repositories.album_repo import AlbumRepository  # noqa: E402
from app.services.artist_service import ArtistService  # noqa: E402
from app.services.search_service import SearchService  # noqa: E402

_TEST_DB_URL = os.environ.get("TEST_DB_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _TEST_DB_URL, reason="TEST_DB_URL not set"),
]


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(_TEST_DB_URL, pool_pre_ping=True, future=True)
    with eng.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT column_name FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'albums'
                   AND column_name IN ('popularity', 'release_date', 'total_tracks')
            """)
        ).fetchall()
    if len({r[0] for r in rows}) < 3:
        eng.dispose()
        pytest.skip("test branch missing albums.popularity / release_date / total_tracks")
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


def _seed_artist_with_twins(session):
    """One artist holding the three shapes the prod measurement found:

    * a market twin pair (same title, same track count, dates one day apart)
      — must collapse to the livelier row;
    * a lead single and its parent album under one title, differing only in
      track count — must BOTH survive;
    * an unrelated album, so a passing test cannot be explained by everything
      collapsing into one row.
    """
    tag = uuid.uuid4().hex[:10]
    artist = Artist(
        id=uuid.uuid4(),
        name=f"Twinmaker{tag}",
        spotify_id=f"sp_art_{tag}",
        popularity=90,
        followers=1000,
    )
    twin_lo = Album(
        id=uuid.uuid4(),
        title=f"Iceman{tag}",
        spotify_id=f"sp_tw_lo_{tag}",
        release_date=date(2026, 5, 15),
        total_tracks=18,
        popularity=75,
    )
    twin_hi = Album(
        id=uuid.uuid4(),
        # deliberately differs by case + padding: normalization must bridge it
        title=f"  ICEMAN{tag.upper()}  ".replace(tag.upper(), tag),
        spotify_id=f"sp_tw_hi_{tag}",
        release_date=date(2026, 5, 16),
        total_tracks=18,
        popularity=99,
    )
    single = Album(
        id=uuid.uuid4(),
        title=f"TwoRoads{tag}",
        spotify_id=f"sp_sgl_{tag}",
        release_date=date(2026, 8, 21),
        total_tracks=1,
        popularity=47,
    )
    album = Album(
        id=uuid.uuid4(),
        title=f"TwoRoads{tag}",
        spotify_id=f"sp_alb_{tag}",
        release_date=date(2026, 8, 28),
        total_tracks=15,
        popularity=57,
    )
    other = Album(
        id=uuid.uuid4(),
        title=f"Unrelated{tag}",
        spotify_id=f"sp_oth_{tag}",
        release_date=date(2020, 1, 1),
        total_tracks=9,
        popularity=30,
    )
    session.add_all([artist, twin_lo, twin_hi, single, album, other])
    session.flush()
    session.execute(album_artists_table.insert().values([
        {"album_id": a.id, "artist_id": artist.id, "role": None}
        for a in (twin_lo, twin_hi, single, album, other)
    ]))
    session.flush()
    return tag, artist, twin_lo, twin_hi, single, album, other


def test_discography_collapses_twins_but_keeps_distinct_releases(session):
    tag, artist, twin_lo, twin_hi, single, album, other = _seed_artist_with_twins(session)

    result = ArtistService(session, AlbumRepository(session)).list_albums_by_artist(
        artist_id=str(artist.id), limit=50, offset=0
    )
    ids = [i.spotify_id for i in result.items]

    assert f"sp_tw_hi_{tag}" in ids, "the livelier twin must survive"
    assert f"sp_tw_lo_{tag}" not in ids, "the duplicate edition must be collapsed away"
    assert f"sp_sgl_{tag}" in ids and f"sp_alb_{tag}" in ids, (
        "a lead single and its parent album share a title — they are different "
        "releases and must both survive"
    )
    assert f"sp_oth_{tag}" in ids
    assert len(ids) == 4


def test_discography_collapse_adds_no_query_per_album(session):
    """The group key reads Album.artists; without the eager load in album_repo
    that is one extra SELECT per album on this path."""
    tag, artist, *_ = _seed_artist_with_twins(session)

    counter = {"n": 0}

    @event.listens_for(session.connection(), "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().lower().startswith(("select", "with")):
            counter["n"] += 1

    ArtistService(session, AlbumRepository(session)).list_albums_by_artist(
        artist_id=str(artist.id), limit=50, offset=0
    )

    # 1 discography SELECT + 1 selectinload for artists (+1 slack for the
    # primary-artist map). Five albums lazy-loading their artists would be 6+.
    assert counter["n"] <= 3, f"expected a bounded query count, got {counter['n']}"


def test_unified_search_returns_one_row_per_release(session):
    """The live defect: `?q=iceman` came back with two identical album rows."""
    tag, *_ = _seed_artist_with_twins(session)

    result = SearchService(session).unified_search(q=f"Iceman{tag}", limit=10, offset=0)
    ids = [a.spotify_id for a in result.albums]

    assert ids.count(f"sp_tw_hi_{tag}") == 1
    assert f"sp_tw_lo_{tag}" not in ids
    assert len(ids) == len(set(ids))


def test_collapse_runs_before_the_limit_is_spent(session):
    """A duplicate must not consume one of the caller's `limit` slots — that was
    the second cost of the defect, not just the visible repetition."""
    tag, artist, twin_lo, twin_hi, single, album, other = _seed_artist_with_twins(session)

    result = ArtistService(session, AlbumRepository(session)).list_albums_by_artist(
        artist_id=str(artist.id), limit=4, offset=0
    )
    ids = [i.spotify_id for i in result.items]

    assert len(ids) == 4, "four distinct releases exist; a page of 4 must show all four"
    assert f"sp_tw_lo_{tag}" not in ids
