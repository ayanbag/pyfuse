"""Token search — the port of ``src/search/token/index.ts``.

Scores each query term independently with Bitap, then combines the hits by
inverse document frequency, so a match on a rare word counts for far more
than a match on a common one.
"""

from __future__ import annotations

import math
from typing import Any, Final

from ...config import FuseOptions
from ...helpers.merge_indices import merge_indices
from ...types import RangeTuple, SearchResult
from ..bitap import BitapSearch
from .analyzer import create_analyzer
from .inverted_index import InvertedIndexData

#: ``token_match="all"`` packs per-term coverage into a bitmask. JS bitwise
#: ops are 32-bit *signed*, so bit 31 is the sign bit and only bits 0..30 are
#: usable. Queries with more terms than this fall back to a set. Python ints
#: have no such limit, but the threshold is preserved: crossing it changes
#: which branch runs, and the branches must agree with the original's.
MAX_MASK_TERMS: Final = 31

__all__ = ["MAX_MASK_TERMS", "TokenSearch"]


class TokenSearch:
    """Matches a multi-term query against text, weighted by IDF."""

    __slots__ = (
        "_analyzer",
        "_combine_all",
        "_idf_weights",
        "_num_terms",
        "_options",
        "_term_searchers",
        "_use_mask",
    )

    def __init__(
        self, pattern: str, options: FuseOptions | dict[str, Any] | None = None
    ) -> None:
        resolved = FuseOptions.coerce(options)
        self._options = resolved
        self._analyzer = create_analyzer(resolved)

        query_terms = self._analyzer.tokenize(pattern)

        inverted_index = resolved.inverted_index
        if inverted_index is None:
            # Token search is meaningless without corpus statistics; an empty
            # index at least yields well-defined (uniform) IDF weights.
            inverted_index = InvertedIndexData()

        self._term_searchers: list[BitapSearch] = []
        self._idf_weights: list[float] = []

        for term in query_terms:
            self._term_searchers.append(
                BitapSearch(
                    term,
                    resolved.replace(
                        # Term position within a field carries no meaning
                        # here — the whole point is order-independent
                        # matching — so location is always ignored.
                        ignore_location=True,
                        inverted_index=None,
                    ),
                )
            )

            doc_freq = inverted_index.df.get(term, 0)
            idf = math.log(
                1 + (inverted_index.field_count - doc_freq + 0.5) / (doc_freq + 0.5)
            )
            self._idf_weights.append(idf)

        self._combine_all = resolved.token_match == "all"
        self._num_terms = len(self._term_searchers)
        self._use_mask = self._num_terms <= MAX_MASK_TERMS

    @staticmethod
    def condition(_pattern: str, options: Any) -> bool:
        """Token search handles the query when the option is enabled."""
        return bool(getattr(options, "use_token_search", False))

    def search_in(self, text: str) -> SearchResult:
        """Match the query terms against ``text``."""
        if not self._term_searchers:
            return SearchResult(is_match=False, score=1.0)

        all_indices: list[RangeTuple] = []
        weighted_score = 0.0
        max_possible_score = 0.0
        matched_count = 0

        # Coverage tracking is untouched on the default "any" path, so it
        # allocates nothing there.
        matched_mask = 0
        matched_terms: set[int] | None = (
            set() if self._combine_all and not self._use_mask else None
        )

        for i, searcher in enumerate(self._term_searchers):
            result = searcher.search_in(text)
            idf = self._idf_weights[i]

            max_possible_score += idf

            if result.is_match:
                matched_count += 1
                weighted_score += idf * (1 - result.score)

                if result.indices:
                    all_indices.extend(result.indices)

                if self._combine_all:
                    if self._use_mask:
                        matched_mask |= 1 << i
                    elif matched_terms is not None:
                        matched_terms.add(i)

        if matched_count == 0:
            return SearchResult(is_match=False, score=1.0)

        normalized = (
            1 - weighted_score / max_possible_score if max_possible_score > 0 else 0.0
        )

        search_result = SearchResult(is_match=True, score=max(0.001, normalized))

        if self._options.include_matches and all_indices:
            search_result.indices = merge_indices(all_indices)

        # Report term coverage so the core loop can enforce a record-level AND.
        if self._combine_all:
            if self._use_mask:
                search_result.matched_mask = matched_mask
            else:
                search_result.matched_terms = matched_terms
            search_result.term_count = self._num_terms

        return search_result
