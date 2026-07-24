"""DATA-release-noise Step 1 — unit tests for the compilation predicate.

The characterization set is drawn from the 2026-07-24 prod window: every catalog
row that motivated the filter (caught) alongside the six genuine classical
performances that share the multi-artist signal but must survive (kept).
"""
from __future__ import annotations

from app.services.compilation_filter import is_compilation_noise

BUDGET = frozenset(
    {"UME - Global Clearing House", "Novus Promusica", "Naxos Special Projects"}
)


def _flag(title, label, n):
    return is_compilation_noise(
        title=title, label=label, n_artists=n, max_artists=10, budget_labels=BUDGET
    )


class TestCaughtCompilations:
    def test_many_artists(self):
        # 13-composer budget comp — the artist-count signal.
        assert _flag("Golden Melodies: Bach, Mozart & more", "UME - Global Clearing House", 13)

    def test_high_artist_count_alone(self):
        # No budget label, no title match, but 11 credited artists.
        assert _flag("Some Programme", "Independent Label", 11)

    def test_budget_label_alone(self):
        # Single composer attributed, low artist count, but a pure-comp label.
        assert _flag('"065 Piano Essentials": Au Printemps', "Novus Promusica", 1)

    def test_numbered_title_alone(self):
        assert _flag("064 Piano Essentials: Country Gardens", "Whatever", 2)
        assert _flag("039 Classical Momentum: Wallenstadt", "Whatever", 3)

    def test_masterpieces_title_family(self):
        assert _flag("Sunrise Prelude: Classical Masterpieces", "Some Label", 5)
        assert _flag("Larimar: Classical Gems", "Some Label", 4)

    def test_and_more_title_family(self):
        assert _flag("A Summer Journey: Bach, Mozart & more", "Some Label", 3)


class TestKeptRealAlbums:
    """Genuine performances / real releases — none may be flagged."""

    def test_named_conductor_requiem(self):
        # 8 performers, non-comp label — the boundary case.
        assert not _flag("Mozart: Requiem; Mass in C Minor", "Deutsche Grammophon (DG)", 8)

    def test_argerich_recital(self):
        assert not _flag("The Chopin & Schumann Recordings", "Warner Classics", 3)

    def test_orff_carmina(self):
        assert not _flag("Orff: Carmina Burana", "Pentatone", 8)

    def test_single_work_concerto(self):
        assert not _flag("Brahms: Violin Concerto in D Major, Op. 77", "Warner Classics", 4)
        assert not _flag("Delius: Violin Concerto & Double Concerto", "Warner Classics", 5)

    def test_pop_releases_untouched(self):
        assert not _flag("new avatar", "Warp Records", 1)
        assert not _flag("CONFESSIONS II: Afterhours Edition", "Warner Records", 1)
        assert not _flag("The Real Me", "Epic", 1)

    def test_none_inputs_are_safe(self):
        assert not _flag(None, None, 0)
