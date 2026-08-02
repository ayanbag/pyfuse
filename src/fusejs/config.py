"""Search options — the port of ``src/core/config.ts``.

Every default here is pinned to fuse.js v7.5.0. Drift in any one of them
silently changes every score the engine produces, so they are covered by
their own parity test rather than trusted to review.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from .search.token.inverted_index import InvertedIndexData

from .helpers.get import get
from .types import (
    FuseOptionKey,
    GetFn,
    InternalResult,
    SortFn,
    TokenizeFn,
)

#: Machine word size — the Bitap pattern-length ceiling.
MAX_BITS: Final = 32


def default_sort_fn(a: InternalResult, b: InternalResult) -> int:
    """Rank by ascending score, breaking ties by original document order.

    Mirrors the original's comparator exactly, including its refusal to ever
    return ``0``: two results with equal scores are ordered by ``idx``, which
    makes the comparator a total order and the sort deterministic regardless
    of the underlying sort's stability guarantees.
    """
    if a.score == b.score:
        return -1 if a.idx < b.idx else 1
    if a.score is None or b.score is None:
        # Unreachable via the public API — `compute_score` populates every
        # result before any sort — but a plugin sorting a half-built result
        # set should fail loudly rather than compare `None` to a float.
        raise ValueError("results must be scored before sorting")
    return -1 if a.score < b.score else 1


@dataclass(slots=True)
class FuseOptions:
    """Configuration for a :class:`~fusejs.Fuse` instance.

    Field names are snake_case, as Python expects. :meth:`from_mapping` also
    accepts the camelCase spellings from the JavaScript API, so a config
    loaded from a shared JSON file works unchanged in both engines.
    """

    # ── Basic ──────────────────────────────────────────────────────
    #: Whether comparisons are case sensitive.
    is_case_sensitive: bool = False
    #: Whether comparisons ignore diacritics (accents).
    ignore_diacritics: bool = False
    #: Whether to include the relevance score in results.
    include_score: bool = False
    #: The keys to search. Strings, path lists, or :class:`KeyOption`s.
    keys: list[FuseOptionKey] = field(default_factory=list)
    #: Whether to sort results by score.
    should_sort: bool = True
    #: Comparator used to order results.
    sort_fn: SortFn = default_sort_fn

    # ── Matching ───────────────────────────────────────────────────
    #: Whether to include match positions in results.
    include_matches: bool = False
    #: Whether to keep scanning after a perfect match is found.
    find_all_matches: bool = False
    #: Only matches at least this long are reported.
    min_match_char_length: int = 1

    # ── Fuzziness ──────────────────────────────────────────────────
    #: Approximately where in the text the pattern is expected.
    location: int = 0
    #: At what point the match algorithm gives up. ``0.0`` demands a perfect
    #: match; ``1.0`` matches anything.
    threshold: float = 0.6
    #: How far from ``location`` a match may stray before it is penalised.
    distance: int = 100

    # ── Advanced ───────────────────────────────────────────────────
    #: Enable the unix-like extended query syntax (``=``, ``^``, ``!``, ...).
    use_extended_search: bool = False
    #: Enable token search with IDF-weighted scoring.
    use_token_search: bool = False
    #: Tokenizer for token search: a compiled pattern or a callable.
    tokenize: re.Pattern[str] | TokenizeFn | None = None
    #: How the words of a multi-word token-search query combine.
    token_match: str = "any"
    #: How to resolve a key path against a document.
    get_fn: GetFn = get
    #: When true, ``location`` and ``distance`` are ignored.
    ignore_location: bool = False
    #: When true, the field-length norm is excluded from scoring.
    ignore_field_norm: bool = False
    #: How strongly the field-length norm affects scoring.
    field_norm_weight: float = 1.0

    #: Score using a transcription of V8's ``Math.pow`` instead of CPython's.
    #:
    #: The two are different functions: CPython's ``**`` is correctly rounded,
    #: V8's is not, and they disagree by 1 ULP on ~10% of calls. Enabling this
    #: trades accuracy and speed (~1600x slower per call) for closer agreement
    #: with fuse.js — useful for differential testing, not for production
    #: search. Off by default: the native result is both faster and strictly
    #: more accurate, and the difference never changed a result's rank in any
    #: measured case. See DECISIONS.md.
    strict_js_pow: bool = False

    #: Internal. Corpus statistics handed to ``TokenSearch``, populated by
    #: :class:`~fusejs.Fuse` when it builds the index — never by user
    #: configuration. fuse.js smuggles the same value through the options
    #: object as ``_invertedIndex``; keeping it a declared field means it
    #: stays typed instead of becoming an untyped bag.
    inverted_index: InvertedIndexData | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.token_match not in ("all", "any"):
            raise ValueError(
                f"token_match must be 'all' or 'any', got {self.token_match!r}"
            )

    def replace(self, **overrides: Any) -> FuseOptions:
        """Return a copy with ``overrides`` applied."""
        return replace(self, **overrides)

    @classmethod
    def from_mapping(cls, source: Mapping[str, Any]) -> FuseOptions:
        """Build options from a mapping, accepting camelCase or snake_case.

        >>> FuseOptions.from_mapping({"includeScore": True}).include_score
        True
        """
        known = {f.name for f in fields(cls)}
        resolved: dict[str, Any] = {}

        for raw_key, value in source.items():
            key = _to_snake_case(raw_key)
            if key not in known:
                raise TypeError(f"Unknown search option: {raw_key!r}")
            resolved[key] = value

        return cls(**resolved)

    @classmethod
    def coerce(cls, source: FuseOptions | Mapping[str, Any] | None) -> FuseOptions:
        """Normalise whatever a caller passed as ``options`` into an instance."""
        if source is None:
            return cls()
        if isinstance(source, cls):
            return source
        if isinstance(source, Mapping):
            return cls.from_mapping(source)
        raise TypeError(
            "options must be a FuseOptions, a mapping, or None; "
            f"got {type(source).__name__}"
        )


_CAMEL_BOUNDARY: Final = re.compile(r"(?<!^)(?=[A-Z])")


def _to_snake_case(name: str) -> str:
    """``"includeScore"`` -> ``"include_score"``; snake_case passes through."""
    return _CAMEL_BOUNDARY.sub("_", name).lower()
