"""Bitap approximate matching — the port of ``src/search/bitap/index.ts``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import MAX_BITS, FuseOptions
from ...helpers.diacritics import strip_diacritics
from ...helpers.merge_indices import merge_indices
from ...types import RangeTuple, SearchResult
from .compute_score import compute_score
from .convert_mask_to_indices import convert_mask_to_indices
from .create_pattern_alphabet import create_pattern_alphabet
from .search import search

__all__ = [
    "MAX_BITS",
    "BitapSearch",
    "compute_score",
    "convert_mask_to_indices",
    "create_pattern_alphabet",
    "search",
]


@dataclass(slots=True, frozen=True)
class _Chunk:
    """One <=32-character slice of a long pattern, with its alphabet."""

    pattern: str
    alphabet: dict[str, int]
    start_index: int


class BitapSearch:
    """Fuzzy-matches one pattern against arbitrary text.

    Patterns longer than the 32-bit machine word are split into overlapping
    chunks, each scanned independently; the per-chunk scores are averaged.

    >>> BitapSearch("old").search_in("Old Man's War").is_match
    True
    >>> round(BitapSearch("old").search_in("Old Man's War").score, 3)
    0.001
    """

    __slots__ = ("chunks", "options", "pattern")

    options: FuseOptions
    pattern: str
    chunks: list[_Chunk]

    def __init__(
        self, pattern: str, options: FuseOptions | dict[str, Any] | None = None
    ) -> None:
        self.options = FuseOptions.coerce(options)

        pattern = pattern if self.options.is_case_sensitive else pattern.lower()
        if self.options.ignore_diacritics:
            pattern = strip_diacritics(pattern)
        self.pattern = pattern

        self.chunks = []

        if not self.pattern:
            return

        length = len(self.pattern)

        if length > MAX_BITS:
            i = 0
            remainder = length % MAX_BITS
            end = length - remainder

            while i < end:
                self._add_chunk(self.pattern[i : i + MAX_BITS], i)
                i += MAX_BITS

            if remainder:
                # The tail chunk overlaps the previous one so it stays a full
                # machine word wide, rather than scanning a short remainder
                # whose accuracy denominator would be tiny.
                start_index = length - MAX_BITS
                self._add_chunk(self.pattern[start_index:], start_index)
        else:
            self._add_chunk(self.pattern, 0)

    def _add_chunk(self, pattern: str, start_index: int) -> None:
        self.chunks.append(
            _Chunk(pattern, create_pattern_alphabet(pattern), start_index)
        )

    @staticmethod
    def condition(pattern: str, options: Any) -> bool:
        """Bitap is the fallback searcher; it accepts everything."""
        return True

    def search_in(self, text: str) -> SearchResult:
        """Match this searcher's pattern against ``text``."""
        options = self.options

        text = text if options.is_case_sensitive else text.lower()
        if options.ignore_diacritics:
            text = strip_diacritics(text)

        # Exact match — skip the scan entirely.
        if self.pattern == text:
            # The whole string is the match, so `min_match_char_length` has to
            # be enforced here too, mirroring `convert_mask_to_indices` on the
            # fuzzy path.
            if len(text) < options.min_match_char_length:
                return SearchResult(is_match=False, score=1.0)

            result = SearchResult(is_match=True, score=0.0)
            if options.include_matches:
                result.indices = [(0, len(text) - 1)]
            return result

        all_indices: list[RangeTuple] = []
        total_score = 0.0
        has_matches = False

        for chunk in self.chunks:
            chunk_result = search(
                text,
                chunk.pattern,
                chunk.alphabet,
                location=options.location + chunk.start_index,
                distance=options.distance,
                threshold=options.threshold,
                find_all_matches=options.find_all_matches,
                min_match_char_length=options.min_match_char_length,
                include_matches=options.include_matches,
                ignore_location=options.ignore_location,
            )

            if chunk_result.is_match:
                has_matches = True

            total_score += chunk_result.score

            if chunk_result.is_match and chunk_result.indices:
                all_indices.extend(chunk_result.indices)

        result = SearchResult(
            is_match=has_matches,
            score=(total_score / len(self.chunks)) if has_matches else 1.0,
        )

        if has_matches and options.include_matches:
            result.indices = merge_indices(all_indices)

        return result
