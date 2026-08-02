"""Bitap core: unit behaviour plus differential equivalence with fuse.js."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fusejs.config import MAX_BITS
from fusejs.errors import PatternLengthError
from fusejs.search.bitap import (
    BitapSearch,
    compute_score,
    convert_mask_to_indices,
    create_pattern_alphabet,
    search,
)

# Astral (non-BMP) characters are excluded: JS strings are UTF-16 code units
# and Python strings are code points, so `"\U0001F600".length` is 2 in JS and
# 1 here. Every index and length in the algorithm inherits that difference.
# See DECISIONS.md — this is a documented, deliberate divergence.
BMP_TEXT = st.text(
    alphabet=st.characters(max_codepoint=0xFFFF, exclude_categories=("Cs",)),
    max_size=60,
)
BMP_PATTERN = st.text(
    alphabet=st.characters(max_codepoint=0xFFFF, exclude_categories=("Cs",)),
    min_size=1,
    max_size=MAX_BITS,
)


class TestPatternAlphabet:
    def test_bit_per_position_read_left_to_right(self):
        assert create_pattern_alphabet("abc") == {"a": 0b100, "b": 0b010, "c": 0b001}

    def test_repeated_characters_accumulate_bits(self):
        assert create_pattern_alphabet("aba") == {"a": 0b101, "b": 0b010}

    def test_empty_pattern(self):
        assert create_pattern_alphabet("") == {}


class TestConvertMaskToIndices:
    def test_collapses_runs(self):
        assert convert_mask_to_indices([1, 1, 0, 1, 1, 1]) == [(0, 1), (3, 5)]

    def test_drops_runs_below_min_length(self):
        assert convert_mask_to_indices([1, 1, 0, 1, 1, 1], 3) == [(3, 5)]

    def test_empty_mask_does_not_wrap_around(self):
        # Python's negative indexing would read the last element where JS
        # reads `undefined`; an empty mask must yield nothing.
        assert convert_mask_to_indices([]) == []

    def test_trailing_run_is_closed(self):
        assert convert_mask_to_indices([0, 1, 1]) == [(1, 2)]


class TestComputeScore:
    def test_perfect_match_scores_zero(self):
        assert compute_score("abc", errors=0, current_location=0) == 0.0

    def test_accuracy_only_when_location_ignored(self):
        assert (
            compute_score("abcd", errors=1, current_location=50, ignore_location=True)
            == 0.25
        )

    def test_zero_distance_makes_any_drift_total(self):
        assert compute_score("abc", errors=0, current_location=5, distance=0) == 1.0
        assert compute_score("abc", errors=0, current_location=0, distance=0) == 0.0

    def test_proximity_penalty_is_added_not_folded(self):
        # The arithmetic order is load-bearing; this pins the exact value.
        assert (
            compute_score("abcd", errors=1, current_location=10, distance=100)
            == 0.25 + 10 / 100
        )


class TestSearchErrors:
    def test_rejects_overlong_pattern(self):
        pattern = "a" * (MAX_BITS + 1)
        with pytest.raises(PatternLengthError, match="exceeds max of 32"):
            search("text", pattern, create_pattern_alphabet(pattern))

    def test_rejects_empty_pattern(self):
        # The original loops forever here; we fail fast instead.
        with pytest.raises(ValueError, match="must not be empty"):
            search("text", "", {})


class TestBitapSearch:
    def test_exact_match_short_circuits(self):
        result = BitapSearch("abc").search_in("abc")
        assert result.is_match
        assert result.score == 0.0

    def test_exact_match_respects_min_match_char_length(self):
        result = BitapSearch("ab", {"min_match_char_length": 3}).search_in("ab")
        assert not result.is_match

    def test_case_insensitive_by_default(self):
        assert BitapSearch("OLD").search_in("Old Man's War").is_match

    def test_case_sensitive_when_requested(self):
        opts = {"is_case_sensitive": True}
        assert not BitapSearch("OLD", opts).search_in("Old Man's War").is_match

    def test_diacritics_ignored_when_requested(self):
        # Fuzzy matching tolerates the accent either way; stripping is what
        # promotes it from an approximate hit to an exact one.
        stripped = BitapSearch("cafe", {"ignore_diacritics": True}).search_in("café")
        assert stripped.is_match
        assert stripped.score == 0.0

        kept = BitapSearch("cafe").search_in("café")
        assert kept.is_match
        assert kept.score > 0.0

    def test_long_pattern_is_chunked(self):
        pattern = "abcdefghijklmnopqrstuvwxyz0123456789"  # 36 > MAX_BITS
        searcher = BitapSearch(pattern)
        assert len(searcher.chunks) == 2
        # The tail chunk overlaps so it stays a full word wide.
        assert len(searcher.chunks[-1].pattern) == MAX_BITS
        assert searcher.search_in(pattern).is_match

    def test_empty_pattern_matches_nothing(self):
        assert not BitapSearch("").search_in("anything").is_match

    def test_include_matches_reports_ranges(self):
        result = BitapSearch("old", {"include_matches": True}).search_in("Old Man")
        assert result.indices == [(0, 2)]


class TestDifferentialAgainstOracle:
    """Every score compared at full float64 precision, not rounded."""

    OPTION_SETS = [
        {},
        {"includeMatches": True},
        {"ignoreLocation": True},
        {"threshold": 0.3},
        {"threshold": 1.0},
        {"distance": 10},
        {"distance": 0},
        {"location": 5},
        {"findAllMatches": True, "includeMatches": True},
        {"minMatchCharLength": 3, "includeMatches": True},
        {"isCaseSensitive": True},
        {"ignoreDiacritics": True},
    ]

    TEXTS = [
        "Old Man's War",
        "The Lock Artist",
        "HTML5",
        "Right Ho Jeeves",
        "The Code of the Wooster",
        "hello world",
        "aaaaaa",
        "",
        "a",
        "The quick brown fox jumps over the lazy dog",
        "café Łódź",
        "x" * 40,
    ]

    PATTERNS = [
        "old",
        "jeeves",
        "hello",
        "xyz",
        "a",
        "the wooster",
        "brown fox",
        "cafe",
        "abcdefghijklmnopqrstuvwxyzabcdefgh",
    ]

    @staticmethod
    def _compare(oracle, pattern, text, options):
        expected = oracle.match(pattern, text, options)
        actual = BitapSearch(pattern, options).search_in(text)

        assert actual.is_match == expected["isMatch"], "isMatch diverged"
        # repr() so the comparison is bit-exact rather than approximate.
        assert repr(actual.score) == repr(float(expected["score"])), "score diverged"

        if "indices" in expected:
            assert actual.indices == [tuple(r) for r in expected["indices"]]
        else:
            assert actual.indices is None

    @pytest.mark.parametrize("options", OPTION_SETS)
    @pytest.mark.parametrize("text", TEXTS)
    def test_example_grid(self, oracle, text, options):
        for pattern in self.PATTERNS:
            self._compare(oracle, pattern, text, options)

    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        text=BMP_TEXT,
        pattern=BMP_PATTERN,
        include_matches=st.booleans(),
        ignore_location=st.booleans(),
        threshold=st.floats(min_value=0.0, max_value=1.0),
        distance=st.integers(min_value=0, max_value=500),
        location=st.integers(min_value=0, max_value=50),
        find_all_matches=st.booleans(),
        min_match_char_length=st.integers(min_value=1, max_value=4),
    )
    def test_property_equivalence(self, oracle, text, pattern, **flags):
        options = {
            "includeMatches": flags["include_matches"],
            "ignoreLocation": flags["ignore_location"],
            "threshold": flags["threshold"],
            "distance": flags["distance"],
            "location": flags["location"],
            "findAllMatches": flags["find_all_matches"],
            "minMatchCharLength": flags["min_match_char_length"],
        }
        self._compare(oracle, pattern, text, options)
