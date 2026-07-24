"""FEAT-release-calendar Track B Step 6 — GET /api/music/releases/calendar.

Repository visibility is exercised against SQLite, while service units use a
stubbed ReleaseEventRepository to cover display soft-grouping on
(artist_id, release_date, normalized title). Router tests mirror
tests/test_feed.py (TestClient + mocked service — param validation, defaults,
Cache-Control on 200 only).
"""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")

FROM = date(2026, 7, 1)
TO = date(2026, 7, 31)


def _client():
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app)


def _row(
    *,
    artist_id,
    source,
    title,
    release_date,
    release_type=None,
    status="announced",
    spotify_album_id=None,
    artist_name="Artist",
    artist_popularity=50,
    album_label=None,
    album_n_artists=0,
):
    return {
        "artist_id": artist_id,
        "source": source,
        "title": title,
        "release_type": release_type,
        "release_date": release_date,
        "status": status,
        "spotify_album_id": spotify_album_id,
        "artist_name": artist_name,
        "artist_popularity": artist_popularity,
        "album_label": album_label,
        "album_n_artists": album_n_artists,
    }


def _svc(rows):
    from app.services.release_calendar_service import ReleaseCalendarService

    svc = ReleaseCalendarService(MagicMock())
    svc.events = MagicMock()
    svc.events.list_events.return_value = rows
    return svc


@pytest.fixture
def sqlite_release_repository():
    from app.repositories.release_event_repo import ReleaseEventRepository

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE artists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    popularity INTEGER
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE artist_release_events (
                    artist_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    release_type TEXT,
                    release_date DATE NOT NULL,
                    status TEXT NOT NULL,
                    spotify_album_id TEXT
                )
                """
            )
        )
        # DATA-release-noise Step 1: the read SQL LEFT JOINs the catalog album
        # (label + credited-artist count) for the compilation filter.
        connection.execute(
            text("CREATE TABLE albums (id TEXT PRIMARY KEY, spotify_id TEXT, label TEXT)")
        )
        connection.execute(
            text("CREATE TABLE album_artists (album_id TEXT NOT NULL, artist_id TEXT NOT NULL)")
        )

    with Session(engine) as db:
        yield ReleaseEventRepository(db), db

    engine.dispose()


@pytest.mark.parametrize(
    ("artist_popularity", "status", "is_public"),
    [
        pytest.param(None, "announced", False, id="unknown-popularity-announced"),
        pytest.param(49, "announced", False, id="low-popularity-announced"),
        pytest.param(50, "announced", True, id="popularity-floor-announced"),
        pytest.param(1, "released", True, id="low-popularity-released"),
    ],
)
def test_public_calendar_visibility_respects_popularity_and_release_status(
    sqlite_release_repository,
    artist_popularity,
    status,
    is_public,
):
    repository, db = sqlite_release_repository
    artist_id = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO artists (id, name, popularity)
            VALUES (:id, :name, :popularity)
            """
        ),
        {
            "id": artist_id,
            "name": "Boundary Artist",
            "popularity": artist_popularity,
        },
    )
    db.execute(
        text(
            """
            INSERT INTO artist_release_events (
                artist_id, source, title, release_type, release_date, status,
                spotify_album_id
            ) VALUES (
                :artist_id, 'musicbrainz', 'Boundary Release', 'album',
                :release_date, :status, NULL
            )
            """
        ),
        {
            "artist_id": artist_id,
            "release_date": date(2026, 7, 18),
            "status": status,
        },
    )

    rows = repository.list_events(date_from=FROM, date_to=TO)

    assert bool(rows) is is_public
    if is_public:
        assert rows[0]["title"] == "Boundary Release"
        assert rows[0]["status"] == status


class TestNormalizeTitle:
    def test_case_and_whitespace_insensitive(self):
        from app.services.release_calendar_service import normalize_title

        assert normalize_title("  The  Album ") == normalize_title("the album")

    def test_strips_itunes_storefront_suffixes(self):
        from app.services.release_calendar_service import normalize_title

        assert normalize_title("Golden - Single") == normalize_title("Golden")
        assert normalize_title("Golden - EP") == normalize_title("golden")
        # only a trailing suffix is stripped — an interior dash stays.
        assert normalize_title("A - Single B") != normalize_title("A B")


class TestCalendarSoftGrouping:
    def test_multi_source_rows_group_once_with_sources_listed(self):
        ar = uuid.uuid4()
        rows = [
            _row(artist_id=ar, source="musicbrainz", title="Golden",
                 release_date=date(2026, 7, 18), release_type="single",
                 artist_name="HUNTR/X", artist_popularity=88),
            _row(artist_id=ar, source="itunes", title="Golden - Single",
                 release_date=date(2026, 7, 18), release_type="single",
                 artist_name="HUNTR/X", artist_popularity=88),
        ]
        res = _svc(rows).get_calendar(date_from=FROM, date_to=TO)
        assert res.total == 1
        ev = res.days[0].events[0]
        assert ev.sources == ["itunes", "musicbrainz"]
        # display title comes from the preferred source (mb over itunes).
        assert ev.title == "Golden"
        assert ev.status == "announced"
        assert ev.release_date == "2026-07-18"

    def test_same_title_different_artists_do_not_group(self):
        rows = [
            _row(artist_id=uuid.uuid4(), source="musicbrainz", title="Golden",
                 release_date=date(2026, 7, 18), artist_name="A"),
            _row(artist_id=uuid.uuid4(), source="musicbrainz", title="Golden",
                 release_date=date(2026, 7, 18), artist_name="B"),
        ]
        res = _svc(rows).get_calendar(date_from=FROM, date_to=TO)
        assert res.total == 2

    def test_same_artist_different_dates_do_not_group(self):
        ar = uuid.uuid4()
        rows = [
            _row(artist_id=ar, source="musicbrainz", title="Golden",
                 release_date=date(2026, 7, 18)),
            _row(artist_id=ar, source="itunes", title="Golden",
                 release_date=date(2026, 7, 19)),
        ]
        res = _svc(rows).get_calendar(date_from=FROM, date_to=TO)
        assert res.total == 2
        assert [d.date for d in res.days] == ["2026-07-18", "2026-07-19"]

    def test_released_row_wins_status_and_spotify_id(self):
        ar = uuid.uuid4()
        rows = [
            _row(artist_id=ar, source="musicbrainz", title="Comet",
                 release_date=date(2026, 7, 3), status="announced"),
            _row(artist_id=ar, source="spotify", title="Comet",
                 release_date=date(2026, 7, 3), status="released",
                 spotify_album_id="sp123", release_type="album"),
        ]
        res = _svc(rows).get_calendar(date_from=FROM, date_to=TO)
        ev = res.days[0].events[0]
        assert ev.status == "released"
        assert ev.spotify_album_id == "sp123"
        assert ev.release_type == "album"
        assert ev.sources == ["musicbrainz", "spotify"]

    def test_release_type_falls_back_to_any_non_null(self):
        ar = uuid.uuid4()
        rows = [
            _row(artist_id=ar, source="musicbrainz", title="X",
                 release_date=date(2026, 7, 5), release_type=None),
            _row(artist_id=ar, source="itunes", title="X",
                 release_date=date(2026, 7, 5), release_type="ep"),
        ]
        res = _svc(rows).get_calendar(date_from=FROM, date_to=TO)
        assert res.days[0].events[0].release_type == "ep"

    def test_day_ordering_and_within_day_popularity_rank(self):
        rows = [
            _row(artist_id=uuid.uuid4(), source="musicbrainz", title="Late",
                 release_date=date(2026, 7, 20), artist_name="Z", artist_popularity=30),
            _row(artist_id=uuid.uuid4(), source="musicbrainz", title="BigDrop",
                 release_date=date(2026, 7, 20), artist_name="A", artist_popularity=95),
            _row(artist_id=uuid.uuid4(), source="musicbrainz", title="Early",
                 release_date=date(2026, 7, 2), artist_name="M", artist_popularity=10),
        ]
        res = _svc(rows).get_calendar(date_from=FROM, date_to=TO)
        assert [d.date for d in res.days] == ["2026-07-02", "2026-07-20"]
        assert [e.title for e in res.days[1].events] == ["BigDrop", "Late"]

    def test_empty_window(self):
        res = _svc([]).get_calendar(date_from=FROM, date_to=TO)
        assert res.days == [] and res.total == 0
        assert res.date_from == "2026-07-01" and res.date_to == "2026-07-31"
        # repo got the exact window
        # (service passes params through unchanged)


class TestCalendarCompilationFilter:
    """DATA-release-noise Step 1: budget classical compilations are hidden;
    genuine classical performances (few artists, non-comp label) pass through."""

    def test_multi_artist_comp_dropped(self):
        rows = [
            _row(artist_id=uuid.uuid4(), source="spotify", status="released",
                 title="Sunrise Prelude: Classical Masterpieces",
                 release_date=date(2026, 7, 6), spotify_album_id="sp1",
                 artist_name="Franz Schubert", artist_popularity=62,
                 album_label="UME - Global Clearing House", album_n_artists=13),
            _row(artist_id=uuid.uuid4(), source="spotify", status="released",
                 title="new avatar", release_date=date(2026, 7, 10),
                 spotify_album_id="sp2", artist_name="Kelela",
                 artist_popularity=57, album_label="Warp Records", album_n_artists=1),
        ]
        res = _svc(rows).get_calendar(date_from=FROM, date_to=TO)
        assert res.total == 1
        assert res.days[0].events[0].title == "new avatar"

    def test_real_classical_performance_survives(self):
        # Named-conductor Requiem: 8 performers, non-comp label — must NOT be hidden.
        rows = [
            _row(artist_id=uuid.uuid4(), source="spotify", status="released",
                 title="Mozart: Requiem; Mass in C Minor", release_date=date(2026, 7, 4),
                 spotify_album_id="sp3", artist_name="Wolfgang Amadeus Mozart",
                 artist_popularity=74, album_label="Deutsche Grammophon (DG)",
                 album_n_artists=8),
        ]
        res = _svc(rows).get_calendar(date_from=FROM, date_to=TO)
        assert res.total == 1
        assert res.days[0].events[0].title == "Mozart: Requiem; Mass in C Minor"

    def test_announced_row_uses_title_signal(self):
        # Pre-confirm row has no joined album (label None, n 0) → title decides.
        rows = [
            _row(artist_id=uuid.uuid4(), source="musicbrainz", status="announced",
                 title="065 Piano Essentials: Au Printemps", release_date=date(2026, 7, 4),
                 artist_name="Franz Schubert", artist_popularity=62),
        ]
        res = _svc(rows).get_calendar(date_from=FROM, date_to=TO)
        assert res.total == 0


class TestCalendarRouter:
    def _mock_service(self, monkeypatch):
        from app.api.routers import releases as releases_router
        from app.domain.schemas import ReleaseCalendarResult

        fake = MagicMock()
        fake.get_calendar.return_value = ReleaseCalendarResult(
            date_from="2026-07-01", date_to="2026-07-31", days=[], total=0
        )
        monkeypatch.setattr(
            releases_router, "ReleaseCalendarService", lambda db: fake
        )
        return fake

    def test_200_explicit_window_sets_cache_control(self, monkeypatch):
        from app.core.cache import SEARCH_CACHE_CONTROL

        fake = self._mock_service(monkeypatch)
        r = _client().get(
            "/api/music/releases/calendar?from=2026-07-01&to=2026-07-31"
        )
        assert r.status_code == 200, r.text
        assert r.headers.get("Cache-Control") == SEARCH_CACHE_CONTROL
        assert r.json() == {
            "date_from": "2026-07-01", "date_to": "2026-07-31",
            "days": [], "total": 0,
        }
        assert fake.get_calendar.call_args.kwargs == {
            "date_from": date(2026, 7, 1), "date_to": date(2026, 7, 31),
        }

    def test_defaults_current_month(self, monkeypatch):
        fake = self._mock_service(monkeypatch)
        r = _client().get("/api/music/releases/calendar")
        assert r.status_code == 200, r.text
        kwargs = fake.get_calendar.call_args.kwargs
        today = date.today()
        assert kwargs["date_from"] == today.replace(day=1)
        assert kwargs["date_to"].month == today.month
        assert (kwargs["date_to"] + timedelta(days=1)).day == 1

    def test_default_to_is_end_of_from_month(self, monkeypatch):
        fake = self._mock_service(monkeypatch)
        r = _client().get("/api/music/releases/calendar?from=2026-02-10")
        assert r.status_code == 200, r.text
        assert fake.get_calendar.call_args.kwargs["date_to"] == date(2026, 2, 28)

    def test_param_validation_422(self, monkeypatch):
        self._mock_service(monkeypatch)
        c = _client()
        # bad ISO date
        assert c.get("/api/music/releases/calendar?from=notadate").status_code == 422
        # from > to
        assert c.get(
            "/api/music/releases/calendar?from=2026-07-31&to=2026-07-01"
        ).status_code == 422
        # window over the 93-day cap
        assert c.get(
            "/api/music/releases/calendar?from=2026-01-01&to=2026-06-01"
        ).status_code == 422
        # 93 days exactly is allowed
        assert c.get(
            "/api/music/releases/calendar?from=2026-01-01&to=2026-04-04"
        ).status_code == 200

    def test_validation_422_is_uncached(self, monkeypatch):
        self._mock_service(monkeypatch)
        r = _client().get(
            "/api/music/releases/calendar?from=2026-07-31&to=2026-07-01"
        )
        assert "Cache-Control" not in r.headers
