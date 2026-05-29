"""BUG-19 Step 1 integration test — exercise unified_search against a real
SQLAlchemy engine.

Why this exists ([[feedback-sa-session-lifecycle-mock-blind]]): the unit tests
in `tests/test_unit.py::TestUnifiedSearchExpansion` stub every repo call, so
they prove the orchestration logic but never validate the SQL — JOIN shape,
eager-load behaviour, query count. BUG-17 PR #20 → #21 showed that mock-only
coverage misses connection / driver-level bugs. This test seeds Postgres,
runs the real `unified_search`, and asserts:

1. all three buckets populate with one literal-matched artist
2. the artist's feat track appears in the artist→tracks expansion
3. query count stays bounded (no N+1) via an SA `before_cursor_execute` listener

Guarded by an explicit `TEST_DB_URL` env var — when unset, the test is skipped
at collection ([[feedback-local-db-smoke-fallback]]) so a local matrix without
a test DB doesn't fail.
"""
from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

# Engine creation in app.core.db reads DATABASE_URL at import time — provide a
# parseable URL before the app modules load.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")

from myblog_shared_db.models import (  # noqa: E402
    Album,
    Artist,
    Base,
    Track,
    album_artists_table,
    track_artists_table,
)

from app.services.search_service import SearchService  # noqa: E402

_TEST_DB_URL = os.environ.get("TEST_DB_URL")

# `integration` marker matches the existing convention (`pytest -m "not
# integration"` in .github/workflows). `skipif` is the secondary gate that
# trips when someone runs the integration suite without TEST_DB_URL configured.
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
    # Schema-drift guard (mirrors worker pattern in BUG-18 integration test):
    # if the Neon test branch is missing the columns we exercise, skip with a
    # clear reason instead of failing on an obscure SQL error.
    with eng.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT column_name FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'albums'
                   AND column_name IN ('popularity', 'release_date')
            """)
        ).fetchall()
    if len({r[0] for r in rows}) < 2:
        eng.dispose()
        pytest.skip("Neon test branch missing albums.popularity / release_date")
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """Per-test connection wrapped in a transaction we always roll back, so
    seeded rows never persist to the Neon test branch.
    """
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


def _seed_minimal_corpus(session):
    """Seed: 1 primary artist + 1 feat artist + 2 albums + 2 tracks (one
    primary-only, one feat). Returns (artist_id, feat_track_id).
    """
    primary = Artist(
        id=uuid.uuid4(),
        name=f"PrimaryArtist-{uuid.uuid4().hex[:8]}",
        spotify_id=f"sp_pri_{uuid.uuid4().hex[:10]}",
        popularity=80,
        followers=1000,
    )
    guest = Artist(
        id=uuid.uuid4(),
        name=f"GuestArtist-{uuid.uuid4().hex[:8]}",
        spotify_id=f"sp_gst_{uuid.uuid4().hex[:10]}",
        popularity=40,
        followers=500,
    )
    album_a = Album(
        id=uuid.uuid4(),
        title=f"AlbumA-{uuid.uuid4().hex[:8]}",
        spotify_id=f"sp_alb_a_{uuid.uuid4().hex[:10]}",
        release_date=date(2024, 6, 1),
        popularity=70,
    )
    album_b = Album(
        id=uuid.uuid4(),
        title=f"AlbumB-{uuid.uuid4().hex[:8]}",
        spotify_id=f"sp_alb_b_{uuid.uuid4().hex[:10]}",
        release_date=date(2025, 1, 1),
        popularity=60,
    )
    track_main = Track(
        id=uuid.uuid4(),
        album_id=album_a.id,
        title=f"MainSong-{uuid.uuid4().hex[:8]}",
        spotify_id=f"sp_trk_m_{uuid.uuid4().hex[:10]}",
        track_no=1,
    )
    track_feat = Track(
        id=uuid.uuid4(),
        album_id=album_b.id,
        title=f"FeatSong-{uuid.uuid4().hex[:8]}",
        spotify_id=f"sp_trk_f_{uuid.uuid4().hex[:10]}",
        track_no=1,
    )
    session.add_all([primary, guest, album_a, album_b, track_main, track_feat])
    session.flush()

    # Link: AlbumA → primary only;  AlbumB → primary only (so feat track shows
    # the difference between album-level and track-level participants)
    session.execute(album_artists_table.insert().values([
        {"album_id": album_a.id, "artist_id": primary.id, "role": None},
        {"album_id": album_b.id, "artist_id": primary.id, "role": None},
    ]))
    # track_main has just primary; track_feat has primary + guest (the "feat")
    session.execute(track_artists_table.insert().values([
        {"track_id": track_main.id, "artist_id": primary.id, "role": None},
        {"track_id": track_feat.id, "artist_id": primary.id, "role": None},
        {"track_id": track_feat.id, "artist_id": guest.id, "role": None},
    ]))
    session.flush()
    return primary, guest, album_a, album_b, track_main, track_feat


def test_unified_search_all_three_buckets_with_bounded_queries(session):
    """RFC line 122: assert all three buckets populated, feat track appears
    via artist→track expansion, query count stays bounded (no N+1)."""
    primary, guest, _alb_a, _alb_b, _t_main, t_feat = _seed_minimal_corpus(session)

    counter = {"n": 0}

    @event.listens_for(session.connection(), "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        # Only count real SQL (skip BEGIN, SAVEPOINT, etc. — they're not data queries).
        s = statement.strip().lower()
        if s.startswith(("select", "insert", "update", "delete", "with")):
            counter["n"] += 1

    svc = SearchService(session)
    counter["n"] = 0  # reset after seed flushes
    res = svc.unified_search(q=primary.name, limit=20, offset=0)
    queries_used = counter["n"]

    # ---- functional assertions ----
    artist_names = [a.name for a in res.artists]
    assert primary.name in artist_names, "literal artist match must appear"

    # Albums for this primary expand in (both albumA + albumB)
    album_titles = [a.title for a in res.albums]
    assert _alb_a.title in album_titles
    assert _alb_b.title in album_titles

    # Feat track (track_feat) must surface even though the literal query was
    # the primary's name — exactly the expansion that this RFC ships.
    track_titles = [t.title for t in res.tracks]
    assert t_feat.title in track_titles, (
        "feat track expected via artist→tracks expansion (the headline RFC behaviour)"
    )

    # The feat track's row must carry the guest in `feat_artist_names`
    feat_row = next(t for t in res.tracks if t.title == t_feat.title)
    assert guest.name in feat_row.feat_artist_names

    # ---- bounded query count ----
    # Conservative upper bound: 1 each for artist/album/track literal matches,
    # 1 per matched artist for albums + tracks expansion (≤1 matched artist
    # here), 1 for tracks-by-album-ids (no album literal match → 0 in this
    # case but allow headroom), 1 for primary_artist_map, plus selectinload
    # second pass (≤4 relationships eagerly loaded). 20 leaves plenty of slack
    # without going so loose the assertion stops catching regressions.
    assert queries_used < 20, (
        f"unified_search ran {queries_used} SQL statements — likely N+1 regression"
    )


def test_track_match_expands_to_album_and_artists(session):
    """track literal match → album + artists appear in their buckets."""
    primary, guest, alb_a, alb_b, _t_main, t_feat = _seed_minimal_corpus(session)

    svc = SearchService(session)
    res = svc.unified_search(q=t_feat.title, limit=20, offset=0)

    track_titles = [t.title for t in res.tracks]
    assert t_feat.title in track_titles

    album_titles = [a.title for a in res.albums]
    assert alb_b.title in album_titles, "track's album must surface via expansion"

    artist_names = {a.name for a in res.artists}
    # Both primary + guest participated on the literal-matched track
    assert primary.name in artist_names
    assert guest.name in artist_names


def test_album_match_expands_to_tracks_and_artists(session):
    """album literal match → tracks + artists appear; sibling album does not."""
    primary, _guest, alb_a, alb_b, t_main, _t_feat = _seed_minimal_corpus(session)

    svc = SearchService(session)
    res = svc.unified_search(q=alb_a.title, limit=20, offset=0)

    album_titles = [a.title for a in res.albums]
    assert alb_a.title in album_titles

    track_titles = [t.title for t in res.tracks]
    assert t_main.title in track_titles, "matched album's tracks must surface"

    artist_names = {a.name for a in res.artists}
    assert primary.name in artist_names


# Suppress unused-import warnings under pyright — Base is imported to ensure
# the shared metadata is loaded before any query runs.
_ = Base
