"""Audit #18 integration — "오늘, 이 앨범들" drops compilations in real SQL.

The home strip served "Major Lazer Essentials" (album_type='compilation') next
to real albums, which read as a playlist. The filter is a WHERE clause, so only
real SQL can prove it — and `album_type` is nullable, so a plain `!=` would also
drop NULL-typed rows. Guarded by `TEST_DB_URL` like the other integration suites.
"""
from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")

from myblog_shared_db.models import Album  # noqa: E402

from app.repositories.album_repo import AlbumRepository  # noqa: E402

_TEST_DB_URL = os.environ.get("TEST_DB_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _TEST_DB_URL, reason="TEST_DB_URL not set"),
]


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(_TEST_DB_URL, pool_pre_ping=True, future=True)
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


def test_on_this_day_excludes_compilations_but_keeps_null_type(session):
    tag = uuid.uuid4().hex[:10]
    released = date(2018, 10, 19)
    rows = {
        kind: Album(
            id=uuid.uuid4(),
            title=f"Otd{kind}{tag}",
            spotify_id=f"sp_otd_{kind}_{tag}",
            release_date=released,
            album_type=None if kind == "none" else kind,
            total_tracks=10,
            popularity=50,
        )
        for kind in ("album", "single", "compilation", "none")
    }
    session.add_all(rows.values())
    session.flush()

    got = AlbumRepository(session).list_on_this_day(
        month=10, day=19, exclude_year=2026, fetch_cap=10_000
    )
    ours = {a.id for a in got} & {a.id for a in rows.values()}

    assert ours == {rows["album"].id, rows["single"].id, rows["none"].id}
