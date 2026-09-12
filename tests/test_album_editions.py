"""DATA-release-noise (c) — unit coverage for the duplicate-edition collapse.

Every case below is a shape taken from the 2026-09-03 prod measurement, not an
invented one: the pairs that must collapse are real Spotify market twins, and
the pairs that must NOT collapse are real distinct releases that a looser key
(the one ``feed_service`` uses for the home strip) would have swallowed.
"""
from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")

from app.services.album_editions import collapse_editions, edition_key  # noqa: E402


def _artist(id_: str):
    return SimpleNamespace(id=id_, name=f"artist-{id_}")


def _album(
    *,
    spotify_id: str,
    title: str,
    artists=("A1",),
    total_tracks: int = 12,
    release_date: date | None = date(2026, 5, 15),
    popularity: int | None = 50,
):
    return SimpleNamespace(
        id=f"uuid-{spotify_id}",
        spotify_id=spotify_id,
        title=title,
        artists=[_artist(a) for a in artists],
        total_tracks=total_tracks,
        release_date=release_date,
        popularity=popularity,
    )


class TestEditionKey:
    def test_case_and_whitespace_do_not_split_a_group(self):
        a = _album(spotify_id="x", title="ICEMAN")
        b = _album(spotify_id="y", title="  iceman  ")
        assert edition_key(a) == edition_key(b)

    def test_internal_whitespace_is_collapsed(self):
        a = _album(spotify_id="x", title="Love  Sick")
        b = _album(spotify_id="y", title="Love Sick")
        assert edition_key(a) == edition_key(b)

    def test_track_count_is_part_of_the_key(self):
        """The whole reason this key is stricter than feed_service's."""
        a = _album(spotify_id="x", title="Bully", total_tracks=3)
        b = _album(spotify_id="y", title="Bully", total_tracks=18)
        assert edition_key(a) != edition_key(b)

    def test_artist_set_is_order_independent(self):
        a = _album(spotify_id="x", title="Split", artists=("A1", "A2"))
        b = _album(spotify_id="y", title="Split", artists=("A2", "A1"))
        assert edition_key(a) == edition_key(b)

    def test_different_artists_same_title_are_different_editions(self):
        a = _album(spotify_id="x", title="Continuum", artists=("A1",))
        b = _album(spotify_id="y", title="Continuum", artists=("A2",))
        assert edition_key(a) != edition_key(b)


class TestCollapse:
    def test_market_twin_collapses_to_one_row(self):
        """GNX: same release, ids one day apart — prod 2026-09-03."""
        lo = _album(spotify_id="a", title="GNX", release_date=date(2024, 11, 21), popularity=75)
        hi = _album(spotify_id="b", title="GNX", release_date=date(2024, 11, 22), popularity=81)
        out = collapse_editions([lo, hi])
        assert len(out) == 1
        assert out[0].spotify_id == "b", "the livelier catalog row must survive"

    def test_lead_single_survives_next_to_its_album(self):
        """Two Roads: 1-track single + 15-track album, same artist."""
        single = _album(spotify_id="s", title="Two Roads", total_tracks=1,
                        release_date=date(2026, 8, 21))
        album = _album(spotify_id="a", title="Two Roads", total_tracks=15,
                       release_date=date(2026, 8, 28))
        out = collapse_editions([single, album])
        assert {a.spotify_id for a in out} == {"s", "a"}

    def test_deluxe_edition_survives_next_to_its_twins(self):
        """SOS Deluxe: LANA — a 38-track edition plus two 42-track duplicates."""
        deluxe38 = _album(spotify_id="d38", title="SOS Deluxe: LANA", total_tracks=38,
                          release_date=date(2024, 12, 20), popularity=79)
        dupA = _album(spotify_id="d42a", title="SOS Deluxe: LANA", total_tracks=42,
                      release_date=date(2025, 2, 8), popularity=47)
        dupB = _album(spotify_id="d42b", title="SOS Deluxe: LANA", total_tracks=42,
                      release_date=date(2025, 2, 9), popularity=68)
        out = collapse_editions([deluxe38, dupA, dupB])
        assert [a.spotify_id for a in out] == ["d38", "d42b"]

    def test_caller_ordering_is_preserved(self):
        """The survivor is emitted where the group FIRST appeared, so a ranking
        the caller already applied is not reshuffled."""
        first = _album(spotify_id="first", title="Alpha", popularity=10)
        other = _album(spotify_id="other", title="Beta", popularity=99)
        twin = _album(spotify_id="twin", title="Alpha", popularity=90)
        out = collapse_editions([first, other, twin])
        assert [a.title for a in out] == ["Alpha", "Beta"]
        assert out[0].spotify_id == "twin"

    def test_null_popularity_loses_to_a_real_one(self):
        none_pop = _album(spotify_id="n", title="Same", popularity=None)
        zero_pop = _album(spotify_id="z", title="Same", popularity=0)
        out = collapse_editions([none_pop, zero_pop])
        assert len(out) == 1 and out[0].spotify_id == "z"

    def test_null_release_date_loses_to_a_real_one(self):
        """Doom Dada: one row carries no release_date at all."""
        undated = _album(spotify_id="u", title="Doom Dada", total_tracks=1,
                         release_date=None, popularity=48)
        dated = _album(spotify_id="d", title="Doom Dada", total_tracks=1,
                       release_date=date(2013, 11, 15), popularity=48)
        out = collapse_editions([undated, dated])
        assert len(out) == 1 and out[0].spotify_id == "d"

    def test_full_tie_breaks_on_spotify_id_not_row_order(self):
        """Talor Stonegrip Smith: two rows identical in every ranking field.
        Whichever order the DB returns them in, the same row must survive."""
        x = _album(spotify_id="zzz", title="Talor", popularity=0, total_tracks=1)
        y = _album(spotify_id="aaa", title="Talor", popularity=0, total_tracks=1)
        assert collapse_editions([x, y])[0].spotify_id == "aaa"
        assert collapse_editions([y, x])[0].spotify_id == "aaa"

    def test_empty_and_single_inputs_are_passthrough(self):
        assert collapse_editions([]) == []
        one = _album(spotify_id="only", title="Only")
        assert collapse_editions([one]) == [one]

    def test_album_with_no_artists_does_not_crash(self):
        orphan = _album(spotify_id="o", title="Orphan", artists=())
        assert collapse_editions([orphan]) == [orphan]
