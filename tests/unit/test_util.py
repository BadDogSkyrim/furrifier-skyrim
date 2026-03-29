"""Tests for utility functions -- hash, color helpers."""

from furrifier.util import (
    hash_string, hash_val, hash_int,
    red_part, green_part, blue_part, alpha_part,
)


class TestHash:
    def test_deterministic(self):
        """Same input always produces same output."""
        assert hash_string('Lydia', 0, 10) == hash_string('Lydia', 0, 10)

    def test_different_seeds(self):
        """Different seeds produce different results (usually)."""
        r1 = hash_string('Lydia', 0, 100)
        r2 = hash_string('Lydia', 1, 100)
        # Not guaranteed different, but very likely with mod 100
        # Just check they're both valid
        assert 0 <= r1 < 100
        assert 0 <= r2 < 100

    def test_mod_zero(self):
        """Mod 0 returns 0."""
        assert hash_string('anything', 0, 0) == 0

    def test_range(self):
        """Result is always in [0, m)."""
        for name in ['Lydia', 'Ulfric', 'Delphine', 'Nazeem', '']:
            for m in [1, 2, 5, 10, 50]:
                r = hash_string(name, 0, m)
                assert 0 <= r < m, f"hash({name!r}, 0, {m}) = {r}"

    def test_empty_string(self):
        """Empty string doesn't crash."""
        r = hash_string('', 0, 10)
        assert 0 <= r < 10

    def test_known_value(self):
        """Verify against hand-computed Pascal result.

        Pascal: h=0, then final h = (31*0) % 16000 = 0, r = 0 % 10 = 0
        for empty string with seed 0.
        """
        assert hash_string('', 0, 10) == 0

    def test_single_char(self):
        """Single char hash matches Pascal logic.

        h = 0 (seed)
        h = (31*0 + ord('A')) % 16000 = 65
        h = (31*65) % 16000 = 2015
        result = 2015 % 10 = 5
        """
        assert hash_string('A', 0, 10) == 5


class TestHashVal:
    def test_range(self):
        """Result is within [min, max]."""
        for name in ['Lydia', 'Ulfric']:
            v = hash_val(name, 0, 1.0, 2.0)
            assert 1.0 <= v <= 2.0


class TestHashInt:
    def test_range(self):
        """Result is within [min, max)."""
        for name in ['Lydia', 'Ulfric']:
            v = hash_int(name, 0, 5, 15)
            assert 5 <= v < 15


class TestColorHelpers:
    def test_red(self):
        assert red_part(0x00BBCCDD) == 0xDD

    def test_green(self):
        assert green_part(0x00BBCCDD) == 0xCC

    def test_blue(self):
        assert blue_part(0x00BBCCDD) == 0xBB

    def test_alpha_full(self):
        assert alpha_part(0xFF000000) == 1.0

    def test_alpha_zero(self):
        assert alpha_part(0x00000000) == 0.0

    def test_alpha_half(self):
        # 0x80 = 128, 128/255 ≈ 0.502
        assert abs(alpha_part(0x80000000) - 128 / 255) < 0.001
