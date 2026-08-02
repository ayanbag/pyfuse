"""Extended search — the port of ``src/search/extended/index.ts``.

Implements the unix-like query syntax: ``=exact``, ``'include``, ``^prefix``,
``suffix$``, their ``!`` inverses, ``|`` for OR, and whitespace for AND.
"""

from __future__ import annotations

from typing import Any

from ...config import FuseOptions
from ...helpers.diacritics import strip_diacritics
from ...helpers.merge_indices import merge_indices
from ...types import RangeTuple, SearchResult
from .matchers import MULTI_MATCH_TYPES, Matcher, is_inverse
from .parse_query import parse_query

__all__ = ["ExtendedSearch"]


class ExtendedSearch:
    """Matches an extended-syntax query against text.

    >>> ExtendedSearch("^old", {"use_extended_search": True}).search_in("old man")
    SearchResult(is_match=True, ...)
    """

    __slots__ = ("options", "pattern", "query")

    def __init__(
        self, pattern: str, options: FuseOptions | dict[str, Any] | None = None
    ) -> None:
        self.options = FuseOptions.coerce(options)

        pattern = pattern if self.options.is_case_sensitive else pattern.lower()
        if self.options.ignore_diacritics:
            pattern = strip_diacritics(pattern)
        self.pattern = pattern

        self.query: list[list[Matcher]] = parse_query(self.pattern, self.options)

    @staticmethod
    def condition(_pattern: str, options: Any) -> bool:
        """Extended search handles the query when the option is enabled."""
        return bool(getattr(options, "use_extended_search", False))

    def search_in(self, text: str) -> SearchResult:
        """Match this query against ``text``.

        Sets ``has_inverse`` when an inverse term contributed to the match.
        :class:`~fusejs.Fuse` uses that to switch key aggregation from "any
        key matches" to "all keys must match" — see fuse.js issue #712.
        """
        if not self.query:
            return SearchResult(is_match=False, score=1.0)

        options = self.options
        text = text if options.is_case_sensitive else text.lower()
        if options.ignore_diacritics:
            text = strip_diacritics(text)

        total_score = 0.0

        # ORs
        for matchers in self.query:
            all_indices: list[RangeTuple] = []
            num_matches = 0
            has_inverse = False

            # ANDs
            for matcher in matchers:
                result = matcher.search(text)

                if not result.is_match:
                    # One failed AND term voids the whole group. `total_score`
                    # is reset here too, matching the original — it is
                    # deliberately *not* reset between OR groups.
                    total_score = 0.0
                    num_matches = 0
                    all_indices = []
                    has_inverse = False
                    break

                num_matches += 1
                total_score += result.score

                if is_inverse(matcher.type):
                    has_inverse = True

                if options.include_matches:
                    if matcher.type in MULTI_MATCH_TYPES:
                        # These report a list of ranges.
                        if isinstance(result.indices, list):
                            all_indices.extend(result.indices)
                    elif isinstance(result.indices, tuple):
                        # The positional matchers report a single range.
                        all_indices.append(result.indices)

            # OR: the first group that matches wins.
            if num_matches:
                result_out = SearchResult(
                    is_match=True, score=total_score / num_matches
                )
                if has_inverse:
                    result_out.has_inverse = True
                if options.include_matches:
                    result_out.indices = merge_indices(all_indices)
                return result_out

        return SearchResult(is_match=False, score=1.0)
