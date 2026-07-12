"""FEAT-release-calendar Track B Step 6 — GET /api/music/releases/calendar.

Service units run over a stubbed ReleaseEventRepository (the SQL WHERE itself
is exercised by prod smoke — no local DB, [[feedback-local-db-smoke-fallback]]);
the seam these tests exercise is the display soft-grouping on
(artist_id, release_date, normalized title). Router tests mirror
tests/test_feed.py (TestClient + mocked service — param validation, defaults,
Cache-Control on 200 only).
"""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock

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
    }


def _svc(rows):
    from app.services.release_calendar_service import ReleaseCalendarService

    svc = ReleaseCalendarService(MagicMock())
    svc.events = MagicMock()
    svc.events.list_events.return_value = rows
    return svc


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
