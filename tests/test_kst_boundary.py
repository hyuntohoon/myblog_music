"""A-4 twin — "today" on this service must be the KST day, not the UTC day.

The backend hit this first (A-4: today's pick vanished between 00:00 and 09:00
KST because reads and writes used Postgres' UTC `current_date`). The same class
of bug lived here in `date.today()`: the Lambda runs UTC, so for those nine
hours every "today" in the read path was still yesterday.

Why these tests are new rather than an extension of the existing ones: every
current test injects `today=...` explicitly, so the *default* branch — the one
production actually takes — was never executed. These cover exactly that branch.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")

import app.core.kst as kst_module
from app.core.kst import kst_today


class _FrozenDatetime(datetime):
    """datetime whose now(tz) is pinned, so the KST boundary is testable."""

    _frozen: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen.astimezone(tz) if tz else cls._frozen


@pytest.fixture
def freeze_clock(monkeypatch):
    def _freeze(utc_moment: datetime):
        frozen = type("_F", (_FrozenDatetime,), {"_frozen": utc_moment})
        monkeypatch.setattr(kst_module, "datetime", frozen)

    return _freeze


# 2026-07-09 KST 00:00 .. 08:59 == 2026-07-08 15:00 .. 23:59 UTC — the window
# where UTC and KST disagree about the date.
KST_EARLY_MORNING_UTC = [
    datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc),   # 00:00 KST
    datetime(2026, 7, 8, 20, 30, tzinfo=timezone.utc),  # 05:30 KST
    datetime(2026, 7, 8, 23, 59, tzinfo=timezone.utc),  # 08:59 KST
]
KST_DAY = date(2026, 7, 9)
UTC_DAY = date(2026, 7, 8)


@pytest.mark.parametrize("utc_moment", KST_EARLY_MORNING_UTC)
def test_kst_today_is_the_kst_day_not_the_utc_day(freeze_clock, utc_moment):
    freeze_clock(utc_moment)

    assert kst_today() == KST_DAY
    assert utc_moment.date() == UTC_DAY, "fixture sanity: UTC is a day behind"


@pytest.mark.parametrize("utc_moment", KST_EARLY_MORNING_UTC)
def test_on_this_day_defaults_to_the_kst_day(freeze_clock, utc_moment):
    """The user-visible one: before 09:00 KST readers got yesterday's slice."""
    from app.services.album_service import AlbumService

    freeze_clock(utc_moment)
    svc = AlbumService(MagicMock())
    svc.albums = MagicMock()
    svc.albums.list_on_this_day.return_value = []

    svc.get_on_this_day(limit=10)

    kwargs = svc.albums.list_on_this_day.call_args.kwargs
    assert (kwargs["month"], kwargs["day"]) == (KST_DAY.month, KST_DAY.day), (
        "on-this-day used the UTC date — readers between 00:00 and 09:00 KST "
        "would see yesterday's albums"
    )
    assert kwargs["exclude_year"] == KST_DAY.year


@pytest.mark.parametrize("utc_moment", KST_EARLY_MORNING_UTC)
def test_new_releases_window_defaults_to_the_kst_day(freeze_clock, utc_moment):
    from app.services.feed_service import FeedService

    freeze_clock(utc_moment)
    svc = FeedService(MagicMock())
    svc.albums = MagicMock()
    svc.albums.list_new_releases.return_value = []
    svc.albums.reviewed_artist_ids.return_value = set()

    svc.get_new_releases(days=30, limit=12)

    since = svc.albums.list_new_releases.call_args.kwargs["since"]
    assert since == KST_DAY - timedelta(days=30), (
        "the rolling window anchored on the UTC date, widening it by a day"
    )


def test_calendar_default_window_is_the_kst_month(freeze_clock, monkeypatch):
    """On the 1st of a month, a UTC "today" defaults the grid to last month.

    Frozen at 2026-08-01 02:00 KST (= 2026-07-31 17:00 UTC): the KST month is
    August, the UTC month is still July.
    """
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.main import app
    import app.api.routers.releases as releases_router

    freeze_clock(datetime(2026, 7, 31, 17, 0, tzinfo=timezone.utc))

    captured = {}

    from app.domain.schemas import ReleaseCalendarResult

    class _StubService:
        def __init__(self, db):
            pass

        def get_calendar(self, **kwargs):
            captured.update(kwargs)
            return ReleaseCalendarResult(
                date_from=str(kwargs["date_from"]),
                date_to=str(kwargs["date_to"]),
                days=[],
                total=0,
            )

    monkeypatch.setattr(releases_router, "ReleaseCalendarService", _StubService)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        TestClient(app).get("/api/music/releases/calendar")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert captured["date_from"] == date(2026, 8, 1), (
        "the default window fell back to the UTC month — on the 1st, visitors "
        "before 09:00 KST would open the calendar on the previous month"
    )
    assert captured["date_to"] == date(2026, 8, 31)
