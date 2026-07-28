# DATA-catalog-noise Step 1a — search_by_title ranks by album popularity.
#
# SQL-shape tests with a fake session capturing the emitted statement (the
# harness style test_album_ingest.py uses in the worker repo): tracks.views is 0
# on every prod row, so the old views-first ORDER BY was a dead signal and
# classical compilations won on created_at volume. These pin (1) the Album join
# exists, (2) albums.popularity DESC NULLS LAST outranks tracks.views, in both
# the pg_trgm and plain branches. Deliberately blind to result mapping — the
# repo returns whatever the session yields.
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.track_repo import TrackRepository
from app.core.config import settings


def _captured_sql(monkeypatch, *, use_trgm: bool) -> str:
    monkeypatch.setattr(settings, "SEARCH_USE_PG_TRGM", use_trgm)
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = []
    repo = TrackRepository(db, artist_repo=MagicMock())
    repo.search_by_title("butterfly", limit=20, offset=0)
    stmt = db.execute.call_args[0][0]
    # postgresql dialect so ILIKE renders as ILIKE (generic dialect lowers it)
    return str(stmt.compile(dialect=postgresql.dialect()))


@pytest.mark.parametrize("use_trgm", [True, False])
def test_search_joins_albums_and_ranks_by_album_popularity(monkeypatch, use_trgm):
    sql = _captured_sql(monkeypatch, use_trgm=use_trgm)
    assert "JOIN albums" in sql
    assert "albums.popularity DESC NULLS LAST" in sql
    # popularity must outrank views (views is the trailing tiebreak, kept only
    # in case the column is ever populated)
    assert sql.index("albums.popularity DESC") < sql.index("tracks.views DESC")


def test_trgm_branch_keeps_substring_first_and_similarity_last(monkeypatch):
    sql = _captured_sql(monkeypatch, use_trgm=True)
    ilike = sql.index("ILIKE")  # substring_match.desc() leads the ORDER BY
    assert sql.rindex("similarity") > sql.index("albums.popularity DESC")
    assert ilike < sql.index("albums.popularity DESC")
