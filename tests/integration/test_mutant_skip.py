"""MUTANT (never merge): a test that only skips, to prove the zero-skip step."""
import pytest

pytestmark = pytest.mark.integration


def test_mutant_skip():
    pytest.skip("mutant: arbitrary skip reason")
