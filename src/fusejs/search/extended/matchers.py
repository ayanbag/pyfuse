"""Extended-query matchers — the port of ``src/search/extended/matchers.ts``.

Each matcher pairs the regexes that recognise its operator with a factory that
builds the matcher itself. **Order matters**: :mod:`~.parse_query` tries each
definition in sequence and takes the first hit, so the fuzzy catch-all must
stay last and ``!^term`` must precede ``!term``.

All the anchors are translated ``\\A``/``\\Z`` rather than ``^``/``$``. Python's
``$`` also matches *before* a trailing newline, which would let ``"=term\\n"``
satisfy an exact-match operator that JS rejects.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from ...config import FuseOptions
from ...types import RangeTuple
from ..bitap import BitapSearch

#: Matcher types that can report more than one index range.
MULTI_MATCH_TYPES: Final = frozenset({"fuzzy", "include"})


def is_inverse(matcher_type: str) -> bool:
    """Whether a matcher type negates its pattern."""
    return matcher_type.startswith("inverse")


@dataclass(slots=True)
class MatcherResult:
    """The outcome of one matcher against one text.

    ``indices`` is a single range for the positional matchers and a list for
    the multi-match ones. The original expresses this with a pair of
    ``as unknown as`` casts; modelling it as a union keeps the two shapes
    distinguishable without an escape hatch, and
    :class:`~fusejs.search.extended.ExtendedSearch` branches on
    :data:`MULTI_MATCH_TYPES` exactly as the original does.
    """

    is_match: bool
    score: float
    indices: RangeTuple | list[RangeTuple]


class Matcher:
    """Matches one extended-query term against text."""

    __slots__ = ("_search", "type")

    def __init__(
        self, matcher_type: str, search: Callable[[str], MatcherResult]
    ) -> None:
        self.type = matcher_type
        self._search = search

    def search(self, text: str) -> MatcherResult:
        """Match this term against ``text``."""
        return self._search(text)


@dataclass(slots=True, frozen=True)
class MatcherDef:
    """A matcher's detection regexes and factory."""

    type: str
    multi_regex: re.Pattern[str]
    single_regex: re.Pattern[str]
    create: Callable[[str, Any], Matcher]


def _exact(pattern: str, _options: Any = None) -> Matcher:
    def search(text: str) -> MatcherResult:
        is_match = text == pattern
        return MatcherResult(is_match, 0.0 if is_match else 1.0, (0, len(pattern) - 1))

    return Matcher("exact", search)


def _include(pattern: str, _options: Any = None) -> Matcher:
    def search(text: str) -> MatcherResult:
        indices: list[RangeTuple] = []
        pattern_len = len(pattern)
        location = 0

        while (index := text.find(pattern, location)) > -1:
            location = index + pattern_len
            indices.append((index, location - 1))

        is_match = bool(indices)
        return MatcherResult(is_match, 0.0 if is_match else 1.0, indices)

    return Matcher("include", search)


def _prefix_exact(pattern: str, _options: Any = None) -> Matcher:
    def search(text: str) -> MatcherResult:
        is_match = text.startswith(pattern)
        return MatcherResult(is_match, 0.0 if is_match else 1.0, (0, len(pattern) - 1))

    return Matcher("prefix-exact", search)


def _inverse_prefix_exact(pattern: str, _options: Any = None) -> Matcher:
    def search(text: str) -> MatcherResult:
        is_match = not text.startswith(pattern)
        return MatcherResult(is_match, 0.0 if is_match else 1.0, (0, len(text) - 1))

    return Matcher("inverse-prefix-exact", search)


def _inverse_suffix_exact(pattern: str, _options: Any = None) -> Matcher:
    def search(text: str) -> MatcherResult:
        is_match = not text.endswith(pattern)
        return MatcherResult(is_match, 0.0 if is_match else 1.0, (0, len(text) - 1))

    return Matcher("inverse-suffix-exact", search)


def _suffix_exact(pattern: str, _options: Any = None) -> Matcher:
    def search(text: str) -> MatcherResult:
        is_match = text.endswith(pattern)
        return MatcherResult(
            is_match,
            0.0 if is_match else 1.0,
            (len(text) - len(pattern), len(text) - 1),
        )

    return Matcher("suffix-exact", search)


def _inverse_exact(pattern: str, _options: Any = None) -> Matcher:
    def search(text: str) -> MatcherResult:
        is_match = text.find(pattern) == -1
        return MatcherResult(is_match, 0.0 if is_match else 1.0, (0, len(text) - 1))

    return Matcher("inverse-exact", search)


def _fuzzy(pattern: str, options: Any = None) -> Matcher:
    resolved = FuseOptions.coerce(options) if options is not None else FuseOptions()
    bitap = BitapSearch(
        pattern,
        FuseOptions(
            location=resolved.location,
            threshold=resolved.threshold,
            distance=resolved.distance,
            include_matches=resolved.include_matches,
            find_all_matches=resolved.find_all_matches,
            min_match_char_length=resolved.min_match_char_length,
            is_case_sensitive=resolved.is_case_sensitive,
            ignore_diacritics=resolved.ignore_diacritics,
            ignore_location=resolved.ignore_location,
        ),
    )

    def search(text: str) -> MatcherResult:
        result = bitap.search_in(text)
        return MatcherResult(result.is_match, result.score, result.indices or [])

    return Matcher("fuzzy", search)


#: Definitions in detection order — the fuzzy catch-all must stay last.
MATCHERS: Final[tuple[MatcherDef, ...]] = (
    # =term — exact match
    MatcherDef("exact", re.compile(r'\A="(.*)"\Z'), re.compile(r"\A=(.*)\Z"), _exact),
    # 'term — include (substring) match
    MatcherDef(
        "include", re.compile(r'\A\'"(.*)"\Z'), re.compile(r"\A'(.*)\Z"), _include
    ),
    # ^term — prefix match
    MatcherDef(
        "prefix-exact",
        re.compile(r'\A\^"(.*)"\Z'),
        re.compile(r"\A\^(.*)\Z"),
        _prefix_exact,
    ),
    # !^term — inverse prefix match
    MatcherDef(
        "inverse-prefix-exact",
        re.compile(r'\A!\^"(.*)"\Z'),
        re.compile(r"\A!\^(.*)\Z"),
        _inverse_prefix_exact,
    ),
    # !term$ — inverse suffix match
    MatcherDef(
        "inverse-suffix-exact",
        re.compile(r'\A!"(.*)"\$\Z'),
        re.compile(r"\A!(.*)\$\Z"),
        _inverse_suffix_exact,
    ),
    # term$ — suffix match
    MatcherDef(
        "suffix-exact",
        re.compile(r'\A"(.*)"\$\Z'),
        re.compile(r"\A(.*)\$\Z"),
        _suffix_exact,
    ),
    # !term — inverse exact (does not contain)
    MatcherDef(
        "inverse-exact",
        re.compile(r'\A!"(.*)"\Z'),
        re.compile(r"\A!(.*)\Z"),
        _inverse_exact,
    ),
    # term — fuzzy match (catch-all, must be last)
    MatcherDef("fuzzy", re.compile(r'\A"(.*)"\Z'), re.compile(r"\A(.*)\Z"), _fuzzy),
)


__all__ = [
    "MATCHERS",
    "MULTI_MATCH_TYPES",
    "Matcher",
    "MatcherDef",
    "MatcherResult",
    "is_inverse",
]
